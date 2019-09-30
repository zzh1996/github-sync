#!/usr/bin/env python3

from config import *
import logging
import coloredlogs
import requests
from collections import namedtuple
import os
import multiprocessing
import subprocess

Repo = namedtuple("Repo", ["name", "url", "description", "id"])


def convert_name(name):
    return name.replace("__", "___").replace("/", "__")


def get_github_stars(github_user):
    page = 1
    stars = []
    while True:
        logging.info("Requesting for page %d", page)
        r = requests.get(
            f"https://api.github.com/users/{github_user}/starred",
            params={"per_page": 100, "page": page},
        )
        r.raise_for_status()
        data = r.json()
        logging.info("Got %d stars", len(data))
        if data:
            stars.extend(
                [
                    Repo(
                        repo["full_name"],
                        repo["clone_url"],
                        repo["description"],
                        repo["id"],
                    )
                    for repo in data
                ]
            )
        else:
            return stars
        page += 1


def get_gitlab_group_namespace():
    r = requests.get(
        f"{gitlab_url}/api/v4/groups/{gitlab_group}",
        headers={"Private-Token": gitlab_token},
    )
    r.raise_for_status()
    return r.json()["id"]


def get_gitlab_repos():
    page = 1
    repos = []
    while True:
        logging.info("Requesting for page %d", page)
        r = requests.get(
            f"{gitlab_url}/api/v4/groups/{gitlab_group}/projects",
            params={"per_page": 100, "page": page},
            headers={"Private-Token": gitlab_token},
        )
        r.raise_for_status()
        data = r.json()
        logging.info("Got %d repos", len(data))
        if data:
            repos.extend(
                [
                    Repo(
                        repo["path"],
                        repo["ssh_url_to_repo"],
                        repo["description"],
                        repo["id"],
                    )
                    for repo in data
                ]
            )
        else:
            return repos
        page += 1


def create_gitlab_repo(namespace, name):
    r = requests.post(
        f"{gitlab_url}/api/v4/projects",
        headers={"Private-Token": gitlab_token},
        data={"path": name, "namespace_id": namespace},
    )
    r.raise_for_status()
    new_repo = r.json()
    return Repo(
        new_repo["path"],
        new_repo["ssh_url_to_repo"],
        new_repo["description"],
        new_repo["id"],
    )


def set_gitlab_repo_description(repo_id, description):
    r = requests.put(
        f"{gitlab_url}/api/v4/projects/{repo_id}",
        headers={"Private-Token": gitlab_token},
        data={"description": description},
    )
    r.raise_for_status()
    return r.json()


def run_command(command):
    logging.debug("Running command: %s", command)
    subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=True,
        check=True,
    )


def syncrepo(github_repo, gitlab_repo, namespace, name):
    try:
        logging.info("Syncing Repo %s", github_repo.name)
        if gitlab_repo is None:
            logging.info("Creating GitLab repo")
            gitlab_repo = create_gitlab_repo(namespace, name)
        if gitlab_repo.description != github_repo.description:
            logging.info("Syncing description")
            set_gitlab_repo_description(gitlab_repo.id, github_repo.description)
        path = f"repos/{name}"
        if os.path.exists(f"{path}/HEAD"):
            logging.info("Fetching GitHub repo")
            run_command(f'git -C "{path}" fetch')
        else:
            logging.info("Creating local repo at %s", path)
            os.makedirs(path, exist_ok=True)
            logging.info("Cloning GitHub repo")
            run_command(f'git clone --bare "{github_repo.url}" "{path}"')
        logging.info("Pushing to GitLab repo")
        run_command(f'git -C "{path}" push --all "{gitlab_repo.url}"')
        run_command(f'git -C "{path}" push --tags "{gitlab_repo.url}"')
        return True
    except Exception as e:
        logging.exception(e)
        return False


def sync():
    github_stars = set()
    for github_user in github_users:
        logging.info("Loading GitHub stars of %s", github_user)
        github_stars |= set(get_github_stars(github_user))
    github_stars = sorted(github_stars, key=lambda repo: repo.name)
    logging.info("Total stars: %d", len(github_stars))
    logging.info("Loading GitLab repos")
    gitlab_repos = get_gitlab_repos()
    logging.info("Total repos: %d", len(gitlab_repos))
    gitlab_repos = {repo.name: repo for repo in gitlab_repos}
    namespace = get_gitlab_group_namespace()
    logging.info("GitLab group namespace: %s", namespace)
    tasks = []
    for github_repo in github_stars:
        name = convert_name(github_repo.name)
        gitlab_repo = gitlab_repos[name] if name in gitlab_repos else None
        tasks.append((github_repo, gitlab_repo, namespace, name))

    pool = multiprocessing.Pool(threads)
    results = pool.starmap(syncrepo, tasks, chunksize=1)
    logging.info("Succeeded: %s/%s", sum(results), len(results))
    failed = [task[0].name for task, result in zip(tasks, results) if not result]
    if failed:
        logging.info("Failed repos: %s", failed)


if __name__ == "__main__":
    coloredlogs.install(
        level=logging.INFO,
        fmt="%(asctime)s.%(msecs)03d %(process)d %(levelname)s %(message)s",
    )
    sync()
