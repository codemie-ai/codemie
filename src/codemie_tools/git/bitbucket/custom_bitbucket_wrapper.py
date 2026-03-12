# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
from urllib.parse import urlencode

from atlassian.bitbucket.cloud import BitbucketCloudBase, Cloud
from atlassian.bitbucket.cloud.repositories import Branches, Repository, PullRequests
from atlassian.bitbucket.cloud.repositories.pullRequests import PullRequest
from requests import HTTPError

logger = logging.getLogger(__name__)


class Sources(BitbucketCloudBase):
    def __init__(self, url, *args, **kwargs):
        """See BitbucketCloudBase."""
        super(Sources, self).__init__(url, *args, **kwargs)

    def request(
        self,
        method="GET",
        path="/",
        data=None,
        json=None,
        flags=None,
        params=None,
        headers=None,
        files=None,
        trailing=None,
        absolute=False,
        advanced_mode=False,
    ):
        """
        Overriding base request method so it's won't use json.dumps on the data
        object that makes it unusable for application/x-www-form-urlencoded requests.
        """
        url = self.url_joiner(None if absolute else self.url, path, trailing)
        params_already_in_url = "?" in url
        if params or flags:
            if params_already_in_url:
                url += "&"
            else:
                url += "?"
        if params:
            url += urlencode(params or {})
        if flags:
            url += ("&" if params or params_already_in_url else "") + "&".join(flags or [])
        self.log_curl_debug(
            method=method,
            url=url,
            headers=headers,
            data=data,
        )
        headers = headers or self.default_headers
        response = self._session.request(
            method=method,
            url=url,
            headers=headers,
            data=data,
            json=json,
            timeout=self.timeout,
            verify=self.verify_ssl,
            files=files,
            proxies=self.proxies,
            cert=self.cert,
        )
        response.encoding = "utf-8"

        logger.debug("HTTP: %s %s -> %s %s", method, path, response.status_code, response.reason)
        logger.debug("HTTP: Response text -> %s", response.text)
        if advanced_mode:
            return response

        self.raise_for_status(response)
        return response

    def create(
        self,
        branch: str,
        commit_message: str,
        file_path_to_delete: str = None,
        file_path_to_create_or_update: str = None,
        file_content_to_create_or_update: str = None,
    ):
        data = {'message': commit_message, 'branch': branch}

        if file_path_to_delete:
            data['files'] = file_path_to_delete

        if file_path_to_create_or_update and file_content_to_create_or_update:
            data[file_path_to_create_or_update] = file_content_to_create_or_update

        headers = {'Content-Type': 'application/x-www-form-urlencoded'}

        return self.post(None, data=data, headers=headers)

    def read_file_or_directory_contents(self, commit_hash: str, file_path: str):
        return self.get(path=f"/{commit_hash}/{file_path}", advanced_mode=True).text


class CustomBranches(Branches):
    def __init__(self, url, *args, **kwargs):
        super(CustomBranches, self).__init__(url, *args, **kwargs)

    def create(
        self,
        name,
        commit,
    ):
        new_branch_name = name

        for i in range(1000):
            try:
                return super(CustomBranches, self).create(name=new_branch_name, commit=commit)
            except HTTPError as e:
                if e.response.status_code == 400 and "BRANCH_ALREADY_EXISTS" in e.response.text:
                    i += 1
                    new_branch_name = f"{name}_v{i}"
                else:
                    # Handle any other exceptions
                    logger.error(f"Failed to create branch. Error: {str(e)}")
                    raise ValueError("Unable to create branch name from proposed_branch_name: " f"{name}")
        raise ValueError(
            "Unable to create branch. "
            "At least 1000 branches exist with named derived from "
            f"proposed_branch_name: `{name}`"
        )


class CustomPullRequest(PullRequest):
    def __init__(self, data, *args, **kwargs):
        super(CustomPullRequest, self).__init__(data, *args, **kwargs)

    def comment(self, raw_message):
        """
        Commenting the pull request in raw format

        API docs: https://developer.atlassian.com/bitbucket/api/2/reference/resource/repositories/%7Bworkspace%7D/%7Brepo_slug%7D/pullrequests/%7Bpull_request_id%7D/comments#post
        """
        if not raw_message:
            raise ValueError("No message set")

        data = {
            "content": {
                "raw": raw_message["content"],
            },
            "inline": {"to": raw_message["line_number"], "path": raw_message["file_path"]},
        }

        return self.post("comments", data)


class CustomPullRequests(PullRequests):
    def __init__(self, url, *args, **kwargs):
        """See BitbucketCloudBase."""
        super(CustomPullRequests, self).__init__(url, *args, **kwargs)

    def __get_object(self, data):
        return CustomPullRequest(data, **self._new_session_args)

    def get(self, id):
        return self.__get_object(super(PullRequests, self).get(id))


class CustomBitbucketApiWrapper(Repository):
    def __init__(
        self, url: str, token: str, project_key: str, repository_slug: str, base_branch: str, active_branch: str
    ):
        if "https://bitbucket.org" in url:
            url = url.replace("bitbucket.org", "api.bitbucket.org")

        cloud = Cloud(url=url, token=token)
        repository = cloud.repositories.get(project_key, repository_slug)

        super(CustomBitbucketApiWrapper, self).__init__(repository.data, **cloud._new_session_args)

        self.base_branch = base_branch
        self.active_branch = active_branch
        self.__sources = Sources(f"{repository.url}/src", **self._new_session_args)
        self.__branches = CustomBranches(f"{self.url}/refs/branches", **self._new_session_args)
        self.__pullrequests = CustomPullRequests(f"{self.url}/pullrequests", **self._new_session_args)

    @property
    def sources(self):
        return self.__sources

    @property
    def branches(self):
        return self.__branches

    @property
    def pullrequests(self):
        return self.__pullrequests
