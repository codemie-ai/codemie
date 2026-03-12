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

import pytest
from unittest.mock import patch, MagicMock

from codemie.datasource.code import code_datasource_processor


@pytest.fixture
def dummy_background_tasks():
    return MagicMock()


@pytest.fixture
def dummy_git_repo():
    repo = MagicMock()
    repo.name = "test_repo"
    repo.index_type = MagicMock()
    return repo


@pytest.fixture
def dummy_user():
    return MagicMock()


@pytest.fixture
def dummy_index():
    return MagicMock()


@patch("codemie.datasource.code.code_datasource_processor.CODE_CONFIG")
@patch("codemie.datasource.code.code_datasource_processor.logger")
@patch("multiprocessing.Process")
def test_run_in_background_multiprocessing_enabled(mock_process, mock_logger, mock_code_config, dummy_background_tasks):
    mock_code_config.enable_multiprocessing = True
    mock_code_config.processing_timeout = 5

    dummy_func = MagicMock()
    dummy_git_repo_name = "repo"
    dummy_proc = MagicMock()
    dummy_proc.is_alive.return_value = False
    mock_process.return_value = dummy_proc

    code_datasource_processor.run_in_background(dummy_func, dummy_git_repo_name, dummy_background_tasks)

    assert dummy_background_tasks.add_task.called
    # Ensure the task runs and process is started and joined
    task = dummy_background_tasks.add_task.call_args[0][0]
    task()  # run the task inline for test
    mock_process.assert_called_once_with(target=dummy_func)
    dummy_proc.start.assert_called_once()
    dummy_proc.join.assert_called_once_with(5)
    # Since is_alive is False, terminate should not be called
    dummy_proc.terminate.assert_not_called()


@patch("codemie.datasource.code.code_datasource_processor.CODE_CONFIG")
@patch("codemie.datasource.code.code_datasource_processor.logger")
def test_run_in_background_no_multiprocessing(mock_logger, mock_code_config, dummy_background_tasks):
    mock_code_config.enable_multiprocessing = False
    dummy_func = MagicMock()
    dummy_git_repo_name = "repo"

    code_datasource_processor.run_in_background(dummy_func, dummy_git_repo_name, dummy_background_tasks)

    assert dummy_background_tasks.add_task.called
    # Run the task inline and check dummy_func is called
    task = dummy_background_tasks.add_task.call_args[0][0]
    task()
    dummy_func.assert_called_once()


@patch("codemie.datasource.code.code_datasource_processor.run_in_background")
@patch("codemie.datasource.code.code_datasource_processor.CodeDatasourceProcessor")
def test_index_code_datasource_in_background(
    mock_processor_cls, mock_run_in_background, dummy_git_repo, dummy_user, dummy_background_tasks
):
    dummy_git_repo.index_type = MagicMock()
    dummy_git_repo.name = "repo"
    dummy_request_uuid = "uuid"

    code_datasource_processor.index_code_datasource_in_background(
        dummy_request_uuid, dummy_git_repo, dummy_user, dummy_background_tasks
    )

    mock_run_in_background.assert_called_once()
    # The first arg to run_in_background is a function (process)
    process_func = mock_run_in_background.call_args[0][0]
    # When called, it should create a processor and call process()
    processor_instance = MagicMock()
    mock_processor_cls.create_processor.return_value = processor_instance
    process_func()
    mock_processor_cls.create_processor.assert_called_once_with(
        git_repo=dummy_git_repo,
        user=dummy_user,
        request_uuid=dummy_request_uuid,
        guardrail_assignments=None,
    )
    processor_instance.process.assert_called_once()


@patch("codemie.datasource.code.code_datasource_processor.IndexInfo")
@patch("codemie.datasource.code.code_datasource_processor.run_in_background")
@patch("codemie.datasource.code.code_datasource_processor.CodeDatasourceProcessor")
def test_update_code_datasource_in_background_resume(
    mock_processor_cls,
    mock_run_in_background,
    mock_index,
    dummy_git_repo,
    dummy_user,
    dummy_index,
    dummy_background_tasks,
):
    dummy_git_repo.index_type = MagicMock()
    dummy_git_repo.name = "repo"
    dummy_request_uuid = "uuid"
    resume_indexing = True
    app_name = "app_name"
    repo_name = "repo_name"
    mock_index.filter_by_project_and_repo.return_value = [dummy_index]

    code_datasource_processor.update_code_datasource_in_background(
        dummy_request_uuid, dummy_git_repo, dummy_user, app_name, repo_name, dummy_background_tasks, resume_indexing
    )

    mock_run_in_background.assert_called_once()
    process_func = mock_run_in_background.call_args[0][0]
    processor_instance = MagicMock()
    mock_processor_cls.create_processor.return_value = processor_instance
    process_func()
    mock_processor_cls.create_processor.assert_called_once_with(
        git_repo=dummy_git_repo,
        user=dummy_user,
        index=dummy_index,
        request_uuid=dummy_request_uuid,
        guardrail_assignments=None,
    )
    processor_instance.resume.assert_called_once()
    processor_instance.reprocess.assert_not_called()


@patch("codemie.datasource.code.code_datasource_processor.IndexInfo")
@patch("codemie.datasource.code.code_datasource_processor.run_in_background")
@patch("codemie.datasource.code.code_datasource_processor.CodeDatasourceProcessor")
def test_update_code_datasource_in_background_reprocess(
    mock_processor_cls,
    mock_run_in_background,
    mock_index,
    dummy_git_repo,
    dummy_user,
    dummy_index,
    dummy_background_tasks,
):
    dummy_git_repo.index_type = MagicMock()
    dummy_git_repo.name = "repo"
    dummy_request_uuid = "uuid"
    resume_indexing = False
    app_name = "app_name"
    repo_name = "repo_name"
    mock_index.filter_by_project_and_repo.return_value = [dummy_index]

    code_datasource_processor.update_code_datasource_in_background(
        dummy_request_uuid, dummy_git_repo, dummy_user, app_name, repo_name, dummy_background_tasks, resume_indexing
    )

    mock_run_in_background.assert_called_once()
    process_func = mock_run_in_background.call_args[0][0]
    processor_instance = MagicMock()
    mock_processor_cls.create_processor.return_value = processor_instance
    process_func()
    mock_processor_cls.create_processor.assert_called_once_with(
        git_repo=dummy_git_repo,
        user=dummy_user,
        index=dummy_index,
        request_uuid=dummy_request_uuid,
        guardrail_assignments=None,
    )
    processor_instance.reprocess.assert_called_once()
    processor_instance.resume.assert_not_called()
