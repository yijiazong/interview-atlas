# -*- coding: utf-8 -*-
"""通过 GitHub REST API 同步本地提交（git push 被网络/代理阻断时的替代通道）。

用法：
    python tools/sync_to_github.py            # 增量同步本地领先的提交
    python tools/sync_to_github.py --force   # 全量重建历史（改写提交后使用）

前置条件：
    1. 已安装 GitHub CLI（gh）并执行过 `gh auth login`（token 需 repo + workflow 权限）；
       若 gh 不在 PATH，可设置环境变量 GH_EXE 指向 gh 可执行文件。
    2. 本地仓库已配置 origin 指向 GitHub 仓库。

适用场景：
    某些网络/代理只放行 api.github.com，封禁 github.com 的 git 智能传输端点，
    导致 `git push` 失败。本脚本走 REST API，重建 blob / tree / commit 对象
    并更新 refs/heads/main，效果等价于 push。由于 Git 对象哈希是确定性的，
    重建出的提交 SHA 与本地完全一致，历史不会被改写。

注意：
    - 只同步「已提交」的内容，未提交的修改不会被带上；
    - 默认增量模式下，若本地与远端历史分叉会拒绝执行，避免覆盖他人工作；
      确需以本地为准（例如刚用 filter-repo 改写过历史）时，用 --force 全量重建；
    - 日常网络正常时，直接用 `git push` 即可，不必用这个脚本。
"""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

API_ROOT = "https://api.github.com"


def find_repo_dir():
    """从脚本位置向上查找包含 .git 的目录。"""
    here = Path(__file__).resolve()
    for cand in [here.parent, *here.parents]:
        if (cand / ".git").exists():
            return cand
    sys.exit("找不到本地 Git 仓库（请把脚本放在仓库内或其子目录）")


REPO_DIR = find_repo_dir()


def git(*args):
    return subprocess.check_output(["git", "-C", str(REPO_DIR), *args],
                                    stderr=subprocess.STDOUT)


def gh_exe():
    """定位 gh：环境变量 / PATH / 随工具安装的本地副本，跨平台且不写死用户名。"""
    exe = os.environ.get("GH_EXE") or shutil.which("gh")
    if exe and Path(exe).exists():
        return exe
    local = Path.home() / ".workbuddy" / "binaries" / "gh-cli" / "bin" / (
        "gh.exe" if os.name == "nt" else "gh")
    if local.exists():
        return str(local)
    sys.exit("找不到 gh：请安装 GitHub CLI 或设置环境变量 GH_EXE")


GH = gh_exe()
url = git("remote", "get-url", "origin").decode().strip()
m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", url)
if not m:
    sys.exit("无法从 origin 解析 GitHub 仓库：" + url)
OWNER, REPO = m.group(1), m.group(2)
R = "/repos/%s/%s" % (OWNER, REPO)

TOKEN = subprocess.check_output([GH, "auth", "token"], text=True).strip()
HEADERS = {
    "Authorization": "Bearer " + TOKEN,
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "interview-atlas-sync",
    "Content-Type": "application/json",
}


def api(path, method="GET", body=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(API_ROOT + path, data=data, method=method, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:500]


def must(path, method="GET", body=None, expect=(200, 201, 202)):
    status, data = api(path, method, body)
    if status not in expect:
        print("API 失败:", method, path, status, str(data)[:400], flush=True)
        sys.exit(1)
    return data


def rebuild_commit(sha, parent, blob_cache):
    """用 Git Data API 重建单个提交，返回新的 commit SHA。"""
    meta = git("show", "-s", "--format=%aI%x00%cI%x00%an%x00%ae%x00%cn%x00%ce",
               sha).decode("utf-8").strip("\n").split("\x00")
    a_date, c_date, a_name, a_email, c_name, c_email = meta
    message = git("cat-file", "commit", sha).partition(b"\n\n")[2].decode("utf-8")

    entries = []
    for item in git("ls-tree", "-r", "-z", sha).split(b"\x00"):
        if not item:
            continue
        head, path = item.split(b"\t", 1)
        mode, typ, blob_sha = head.decode().split(" ")
        if typ != "blob":
            continue
        if blob_sha not in blob_cache:
            content = git("cat-file", "blob", blob_sha)
            out = must(R + "/git/blobs", "POST",
                       {"content": base64.b64encode(content).decode("ascii"),
                        "encoding": "base64"})
            blob_cache[blob_sha] = out["sha"]
        entries.append({"path": path.decode("utf-8"), "mode": mode,
                        "type": "blob", "sha": blob_cache[blob_sha]})

    tree = must(R + "/git/trees", "POST", {"tree": entries})
    commit = must(R + "/git/commits", "POST", {
        "message": message,
        "tree": tree["sha"],
        "parents": [parent] if parent else [],
        "author": {"name": a_name, "email": a_email, "date": a_date},
        "committer": {"name": c_name, "email": c_email, "date": c_date},
    })
    return commit["sha"], len(entries), message.splitlines()[0][:44]


def main(force=False):
    status, data = api(R + "/git/ref/heads/main")
    if status == 200:
        remote_sha = data["object"]["sha"]
    elif status in (404, 409):
        # 空仓库不允许直接创建 Git 对象，先用一个占位提交初始化
        out = must(R + "/contents/README.md", "PUT", {
            "message": "Initialize repository",
            "content": base64.b64encode(b"# initialized\n").decode("ascii"),
            "branch": "main",
        }, expect=(200, 201))
        remote_sha = out["commit"]["sha"]
    else:
        sys.exit("无法读取远端 main：%s %s" % (status, str(data)[:200]))

    local_head = git("rev-parse", "HEAD").decode().strip()
    if not force and remote_sha == local_head:
        print("远端 main 已是最新：", remote_sha[:12], flush=True)
        return

    if force:
        new_shas = git("rev-list", "--reverse", "HEAD").decode().strip().splitlines()
        prev = None
        print("全量重建：%d 个提交 | %s/%s" % (len(new_shas), OWNER, REPO), flush=True)
    else:
        try:
            git("cat-file", "-e", remote_sha + "^{commit}")
        except subprocess.CalledProcessError:
            sys.exit("远端 main 有本地不存在的提交，请先 git fetch 或改用 --force")
        if subprocess.run(["git", "-C", str(REPO_DIR), "merge-base",
                            "--is-ancestor", remote_sha, "HEAD"]).returncode != 0:
            sys.exit("本地与远端历史已分叉，请人工确认或用 --force 以本地为准")
        new_shas = git("rev-list", "--reverse", remote_sha + "..HEAD").decode().strip().splitlines()
        prev = remote_sha
        print("增量同步：%d 个提交 | %s/%s" % (len(new_shas), OWNER, REPO), flush=True)

    blob_cache = {}
    for i, sha in enumerate(new_shas, 1):
        prev, nfiles, subject = rebuild_commit(sha, prev, blob_cache)
        print("  [%d/%d] %s | 文件 %d | SHA 一致 %s | %s"
              % (i, len(new_shas), sha[:10], nfiles, prev == sha, subject), flush=True)

    must(R + "/git/refs/heads/main", "PATCH", {"sha": prev, "force": True}, expect=(200,))
    if prev == local_head:
        git("update-ref", "refs/remotes/origin/main", prev)

    s, commits = api(R + "/commits?per_page=5")
    s2, tree = api(R + "/git/trees/main?recursive=1")
    nfiles = len([t for t in tree.get("tree", []) if t["type"] == "blob"]) if isinstance(tree, dict) else "?"
    print("完成：远端 main =", prev[:12],
          "| 最近提交数:", len(commits) if isinstance(commits, list) else "?",
          "| 文件数:", nfiles, flush=True)
    print("仓库地址：https://github.com/%s/%s" % (OWNER, REPO), flush=True)


if __name__ == "__main__":
    main(force="--force" in sys.argv[1:])
