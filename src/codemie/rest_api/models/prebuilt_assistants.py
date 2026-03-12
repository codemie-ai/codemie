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

from typing import List, Optional

from codemie_tools.cloud.toolkit import CloudToolkitUI
from codemie_tools.code.sonar.tools_vars import SONAR_TOOL
from codemie_tools.core.project_management.toolkit import ProjectManagementToolkitUI
from codemie_tools.git.toolkit import GitToolkitUI
from codemie_tools.core.vcs.toolkit import VcsToolkitUI
from codemie_tools.core.vcs.gitlab.tools_vars import GITLAB_TOOL
from codemie_tools.notification.toolkit import NotificationToolkitUI
from codemie_tools.notification.email.tools import EMAIL_TOOL

from codemie.agents.tools import CodeToolkitUI
from codemie_tools.research.toolkit import ResearchToolkitUI
from codemie_tools.base.models import ToolKit, Tool
from codemie_tools.data_management.file_system.toolkit import FileSystemToolkitUI
from codemie_tools.data_management.file_system.tools_vars import (
    READ_FILE_TOOL,
    WRITE_FILE_TOOL,
    LIST_DIRECTORY_TOOL,
    COMMAND_LINE_TOOL,
)
from codemie_tools.git.github.tools_vars import (
    GET_PR_CHANGES,
    CREATE_PR_CHANGE_COMMENT,
    SET_ACTIVE_BRANCH_TOOL,
    CREATE_FILE_TOOL,
    UPDATE_FILE_TOOL,
    DELETE_FILE_TOOL,
    CREATE_PULL_REQUEST_TOOL,
    CREATE_GIT_BRANCH_TOOL,
    LIST_BRANCHES_TOOL,
)
from codemie.enterprise.plugin import get_plugin_toolkit_ui_info
from codemie_tools.core.project_management.confluence.tools_vars import GENERIC_CONFLUENCE_TOOL
from codemie_tools.core.project_management.jira.tools_vars import GENERIC_JIRA_TOOL
from codemie.rest_api.models.assistant import AssistantBase, ToolKitDetails
from codemie.rest_api.security.user import User
from codemie.service.llm_service.llm_service import llm_service
from codemie.configs import logger
from codemie.templates.agents.business_analyst import BA_SYSTEM_PROMPT
from codemie.templates.agents.cloud_agent import CLOUD_AGENT_PROMPT
from codemie.templates.agents.core_prompts import (
    ONBOARDING_PROMPT,
    CODEMIE_ONBOARDING_PROMPT,
    CODEMIE_JIRA_PROMPT,
    CONFLUENCE_PROMPT_TEMPLATE,
    LOCAL_UNIT_TEST_PROMPT,
    LOCAL_FE_UNIT_TEST_PROMPT,
    CODEMIE_LOCAL_UI_DEVELOPMENT_PROMPT,
    CSV_ANALYST_PROMPT,
    CODEMIE_LOCAL_BACKEND_DEVELOPMENT_PROMPT,
    LOCAL_PLUGIN_DEVELOPER_PROMPT,
    QA_CHECKLIST_GENERATOR,
    QA_TESTCASES_GENERATOR,
    RELEASE_MANAGER_PROMPT,
    AQA_TESTCASES_GENERATOR_WITH_BACKEND_CODE,
    SONAR_RETRIEVER,
    AQA_TESTCASES_GENERATOR_UI_TESTS,
    AQA_AC_BDD_GENERATOR_UI_TESTS,
)
from codemie.templates.agents.developer_agent_prompts import (
    CODE_REVIEWER_PROMPT,
    PYTHON_DEVELOPER_SYSTEM_PROMPT,
    FRONTEND_VUE_DEVELOPER_SYSTEM_PROMPT,
    DESIGN_TO_CODE_SYSTEM_PROMPT,
)
from codemie.templates.agents.gitlab_ci_agent import GITLAB_CI_AGENT_PROMPT
from codemie.templates.agents.documentation import (
    IMPLEMENTATION_DETAILS_SYSTEM_PROMPT,
    IMPLEMENTATION_DETAILS_DESCRIPTION,
)
from codemie.templates.agents.project_management import (
    NOTIFICATION_SENDER_SYSTEM_PROMPT,
    NOTIFICATION_SENDER_DESCRIPTION,
    NEWSLETTER_SYSTEM_PROMPT,
    NEWSLETTER_DESCRIPTION,
)
from codemie.templates.agents.pull_request_review_prompts import (
    PULL_REQUEST_REVIEWER_PROMPT,
    PULL_REVIEWER_SUMMARIZER_PROMPT,
    PULL_REQUEST_FILE_PROMPT,
)
from codemie.templates.agents.researcher_prompts import RESEARCHER_PROMPT

CODE_REVIEW_PNG_ICON = "https://epam-gen-ai-run.github.io/ai-run-install/docs/assets/ai/code_reviewer.png"
QA_TESTER_ICON_URL = "https://epam-gen-ai-run.github.io/ai-run-install/docs/assets/ai/qa_tester.png"
FRONTEND_VUE_DEVELOPER_ICON_URL = (
    "https://epam-gen-ai-run.github.io/ai-run-install/docs/assets/ai/developer-frontend-jane.png"
)
DEVELOPER_ICON_URL = "https://epam-gen-ai-run.github.io/ai-run-install/docs/assets/ai/developer-python-nicolas.png"

FRONTEND_VUE_DEVELOPER_DESCRIPTION = """
Role:
The Junior Javascript Vue Developer is responsible for developing high-quality, dynamic, and responsive web applications using Vue.js. This role involves translating designs into real-world applications, optimizing performance to adhere to best practices in Vue.js development.

Brief Background/Bio:
This persona represents a developer early in their career with a passion for front-end development, specifically in using Vue.js to bring web interfaces to life. They have a foundational understanding of JavaScript and able to apply their skills in real life.

Key Skills and Technologies:
Proficient in JavaScript, including ES6+ syntax.
Experience with Vue.js and its core principles such as components, reactivity
Familiarity with Vue.js ecosystem, including Vue Router, Vuex, Toastify, Tailwindcss frameworks
Experience in pre-processors such as SASS
Knowledge of consuming RESTful APIs

Possible Challenges They Might Face:
Integrating new Vue.js features and best practices into their workflow
Ensuring code quality and maintainability while maintaining high development speed
Optimizing and refactoring the codebase
""".strip()

DESIGN_TO_CODE_DESCRIPTION = """
Role:
The Frontend Developer, design to code expert is responsible for developing high-quality, dynamic, and responsive web applications. This role involves translating designs into real-world applications, optimizing performance to adhere to best practices in frontend development.

Brief Background/Bio:
This persona represents a developer early in their career with a passion for front-end development. They have a JavaScript/HTML/CSS skills and able to apply their skills in real life.

Key Skills and Technologies:
Proficient in JavaScript, including ES6+ syntax.
Experience with React/Angular/Vue.js and its core principles such as components, reactivity
Familiarity with React/Angular/Vue.js ecosystem, including React/Redux/Zutang/ngrx/Vue Router, Vuex, Pinia, Toastify, Tailwindcss frameworks
Experience in pre-processors such as SASS.
Expert in atomic design. Storybook tool expert.
Knowledge of consuming RESTful APIs

Possible Challenges They Might Face:
Transform designs into real-world applications, including ES6+ syntax.
Generate html by description, generate react/angual/vue web application based on image of html. Make components based on a html markup.
Integrating new frontend features and best practices into their workflow.
Generate stories for storybook.
Ensuring code quality and maintainability while maintaining high development speed
Optimizing and refactoring the codebase
""".strip()

PYTHON_DEVELOPER_DESCRIPTION = """
Role:
As a Junior Python Langchain Developer, the individual is responsible for contributing to the development of applications and tools within the Langchain framework. This role emphasizes the use of Python for creating efficient, reliable language chain applications, incorporating frameworks such as Langchain, Pydantic, and Tiktoken for enhanced functionality and performance.

Brief Background/Bio:
This persona encapsulates an early-career developer with a substantial interest in Python programming and its application in developing language chain technologies. With a basic foundation in Python and a keenness to delve into advanced frameworks, they are poised to tackle the challenges of creating sophisticated language processing tools.

Key Skills and Technologies:
Proficient in Python, with a solid understanding of its syntax and core libraries.
Hands-on experience with the Langchain framework, demonstrating the ability to leverage its capabilities in project development.
Familiarity with Pydantic for data validation and settings management using Python type annotations.
Experience with Tiktoken for token management and authorization within Python applications.
Knowledge of integrating and utilizing Python frameworks to solve complex programming challenges.
Familiarity with version control systems, such as Git, for effective source code management.

Possible Challenges They Might Face:
Mastering the specific functionalities and best practices associated with the Langchain, Pydantic, and Tiktoken frameworks.
Adapting to the rapid pace of development within the language chain technology space and staying abreast of the latest advancements.
Ensuring high-quality code that is both efficient and scalable while meeting project deadlines.
""".strip()


def init_developer_with_local_fs_toolkit() -> List[ToolKit]:
    return [
        VcsToolkitUI(tools=[Tool.from_metadata(GITLAB_TOOL)]),
        ProjectManagementToolkitUI(
            tools=[
                Tool.from_metadata(GENERIC_JIRA_TOOL),
            ]
        ),
        FileSystemToolkitUI(
            tools=[
                Tool.from_metadata(READ_FILE_TOOL),
                Tool.from_metadata(WRITE_FILE_TOOL),
                Tool.from_metadata(LIST_DIRECTORY_TOOL),
                Tool.from_metadata(COMMAND_LINE_TOOL),
            ]
        ),
    ]


def init_autonomous_developer_toolkit() -> List[ToolKit]:
    return [
        ProjectManagementToolkitUI(
            tools=[
                Tool.from_metadata(GENERIC_JIRA_TOOL),
            ]
        ),
        GitToolkitUI(
            tools=[
                Tool.from_metadata(SET_ACTIVE_BRANCH_TOOL),
                Tool.from_metadata(CREATE_FILE_TOOL),
                Tool.from_metadata(UPDATE_FILE_TOOL),
                Tool.from_metadata(DELETE_FILE_TOOL),
                Tool.from_metadata(CREATE_PULL_REQUEST_TOOL),
                Tool.from_metadata(CREATE_GIT_BRANCH_TOOL),
                Tool.from_metadata(LIST_BRANCHES_TOOL),
            ]
        ),
    ]


def cast_to_toolkit_details(toolkits: List[ToolKit]) -> List[ToolKitDetails]:
    """
    Convert toolkit instances or dicts to ToolKitDetails.

    Handles both ToolKit model instances and dict representations (from enterprise
    dependency functions). Preserves extra fields like name, description, and
    user_description from enterprise toolkits.
    Filters out None values with logging.
    """
    result = []
    for toolkit in toolkits:
        if toolkit is None:
            logger.warning("Skipping None toolkit in cast_to_toolkit_details")
            continue

        # Handle dict (from get_tools_ui_info() calls)
        if isinstance(toolkit, dict):
            result.append(ToolKitDetails(**toolkit))
        # Handle ToolKit instance (from enterprise wrappers)
        else:
            toolkit_dict = toolkit.model_dump()
            # ToolKitDetails will automatically include all fields from the dict
            # Extra fields (name, description, user_description) are preserved
            # if ToolKitDetails model allows them
            result.append(ToolKitDetails(**toolkit_dict))

    return result


class PrebuiltAssistant(AssistantBase):
    slug: str
    video_link: Optional[str] = None

    @classmethod
    def prebuilt_assistants(cls, user: User) -> List[AssistantBase]:
        user_project = user.current_project
        jira_assistant = PrebuiltAssistant(
            name="[Template] Epic/User story Composer",
            slug="template-epic-user-story-composer-assistant",
            description="""
            Prebuilt Business Analyst Assistant. Main role is to analyze and generate requirements, create epics, user stories in Jira.
            """.strip(),
            system_prompt=BA_SYSTEM_PROMPT.strip(),
            toolkits=cast_to_toolkit_details(
                [
                    (
                        ProjectManagementToolkitUI(
                            tools=[
                                Tool.from_metadata(GENERIC_JIRA_TOOL),
                            ]
                        )
                    ),
                ]
            ),
            is_react=False,
            icon_url="https://epam-gen-ai-run.github.io/ai-run-install/docs/assets/ai/BA_Bettie.png",
            project=user_project,
        )
        python_developer_assistant = PrebuiltAssistant(
            name="Junior Python Langchain Developer",
            slug="template-python-langchain-developer-assistant",
            description=PYTHON_DEVELOPER_DESCRIPTION,
            system_prompt=PYTHON_DEVELOPER_SYSTEM_PROMPT.strip(),
            toolkits=cast_to_toolkit_details(init_autonomous_developer_toolkit()),
            icon_url=DEVELOPER_ICON_URL,
            is_react=False,
            project=user_project,
        )
        frontend_vue_developer_assistant = PrebuiltAssistant(
            name="Junior Javascript Vue Developer",
            slug="template-javascript-vue-developer-assistant",
            description=FRONTEND_VUE_DEVELOPER_DESCRIPTION,
            system_prompt=FRONTEND_VUE_DEVELOPER_SYSTEM_PROMPT.strip(),
            toolkits=cast_to_toolkit_details(init_autonomous_developer_toolkit()),
            icon_url=FRONTEND_VUE_DEVELOPER_ICON_URL,
            is_react=False,
            project=user_project,
        )
        design_to_code_assistant = PrebuiltAssistant(
            name="Design to Code Developer",
            slug="template-design-to-code-developer-assistant",
            description=DESIGN_TO_CODE_DESCRIPTION,
            system_prompt=DESIGN_TO_CODE_SYSTEM_PROMPT.strip(),
            toolkits=cast_to_toolkit_details(init_developer_with_local_fs_toolkit()),
            icon_url=FRONTEND_VUE_DEVELOPER_ICON_URL,
            is_react=False,
            project=user_project,
        )
        code_reviewer = PrebuiltAssistant(
            name="[Template] Code Reviewer",
            slug="template-code-reviewer-assistant",
            description="""
            Prebuilt Code Reviewer. Main role is to review changes in Pull Requests and create comments on its findings.
            """.strip(),
            system_prompt=CODE_REVIEWER_PROMPT.strip(),
            toolkits=cast_to_toolkit_details(
                [GitToolkitUI(tools=[Tool.from_metadata(GET_PR_CHANGES), Tool.from_metadata(CREATE_PR_CHANGE_COMMENT)])]
            ),
            icon_url=CODE_REVIEW_PNG_ICON,
            is_react=False,
            project=user_project,
        )
        researcher = PrebuiltAssistant(
            name="[Template] Google Search Assistant",
            slug="template-google-search-assistant",
            description="""Prebuilt Research Assistant. Assistant can browse internet, web scrapping and provide research report.""",
            system_prompt=RESEARCHER_PROMPT.strip(),
            toolkits=cast_to_toolkit_details([ResearchToolkitUI()]),
            icon_url="https://epam-gen-ai-run.github.io/ai-run-install/docs/assets/ai/researcher.png",
            is_react=False,
            project=user_project,
        )
        documentation_assistant = PrebuiltAssistant(
            name="[Template] High-level implementation details",
            slug="template-implementation-details-assistant",
            description=IMPLEMENTATION_DETAILS_DESCRIPTION,
            system_prompt=IMPLEMENTATION_DETAILS_SYSTEM_PROMPT.strip(),
            toolkits=cast_to_toolkit_details(
                [
                    VcsToolkitUI(tools=[Tool.from_metadata(GITLAB_TOOL)]),
                ]
            ),
            icon_url="https://static.vecteezy.com/system/resources/previews/021/147/856/non_2x/implement-icon-design-free-vector.jpg",
            is_react=False,
            project=user_project,
        )
        notifications_assistant = PrebuiltAssistant(
            name="[Template] Notification sender assistant",
            slug="template-notification-assistant",
            description=NOTIFICATION_SENDER_DESCRIPTION,
            system_prompt=NOTIFICATION_SENDER_SYSTEM_PROMPT.strip(),
            toolkits=cast_to_toolkit_details(
                [
                    NotificationToolkitUI(tools=[Tool.from_metadata(EMAIL_TOOL)]),
                ]
            ),
            icon_url="https://epam-gen-ai-run.github.io/ai-run-install/docs/assets/ai/EmailSendingAssistant.jpg",
            is_react=False,
            project=user_project,
        )
        newsletter_assistant = PrebuiltAssistant(
            name="[Template] Product Release Newsletter",
            slug="template-newsletter-assistant",
            description=NEWSLETTER_DESCRIPTION,
            system_prompt=NEWSLETTER_SYSTEM_PROMPT.strip(),
            toolkits=cast_to_toolkit_details(
                [
                    ProjectManagementToolkitUI(
                        tools=[
                            Tool.from_metadata(GENERIC_JIRA_TOOL),
                        ]
                    ),
                ]
            ),
            icon_url="https://epam-gen-ai-run.github.io/ai-run-install/docs/assets/ai/ReleaseSummaryWriter.jpg",
            is_react=False,
            project=user_project,
        )
        chatgpt = PrebuiltAssistant(
            name="[Template] ChatGPT",
            slug="template-chatgpt-assistant",
            description="This is simple chatbot. This is alternative to ChatGPT.",
            system_prompt="You are a nice chatbot having a conversation with a human.",
            toolkits=[],
            is_react=False,
            icon_url="https://epam-gen-ai-run.github.io/ai-run-install/docs/assets/ai/openai-icon.png",
            project=user_project,
        )
        onboarding_assistant = PrebuiltAssistant(
            name="[Template] Project Onboarding Assistant",
            slug="template-project-onboarding-assistant",
            description="""
            This is simple chatbot over own project specific knowledge base, e.g. knowledge base or code repository.
            This is useful when you want to ask questions about your codebase, knowledge base or project specific questions.
            """.strip(),
            system_prompt=ONBOARDING_PROMPT,
            toolkits=[],
            is_react=False,
            icon_url="https://epam-gen-ai-run.github.io/ai-run-install/docs/assets/ai/onboarding_assistant.png",
            project=user_project,
        )
        cloud_assistant = PrebuiltAssistant(
            name="[Template] Cloud Assistant",
            slug="template-cloud-assistant",
            description="""Prebuilt Cloud Assistant. Main role is to help with cloud systems development and interactions.""",
            system_prompt=CLOUD_AGENT_PROMPT.strip(),
            toolkits=cast_to_toolkit_details([CloudToolkitUI()]),
            icon_url="https://epam-gen-ai-run.github.io/ai-run-install/docs/assets/ai/researcher.png",
            is_react=False,
            project=user_project,
        )
        confluence_assistant = PrebuiltAssistant(
            name="[Template] Confluence Assistant",
            slug="template-confluence-assistant",
            description="""Prebuilt Confluence Assistant. Can help with Confluence operations, like smart search, page creation, etc.""",
            system_prompt=CONFLUENCE_PROMPT_TEMPLATE.strip(),
            toolkits=cast_to_toolkit_details(
                [
                    ProjectManagementToolkitUI(
                        tools=[
                            Tool.from_metadata(GENERIC_CONFLUENCE_TOOL),
                        ]
                    ),
                ]
            ),
            icon_url="https://res.cloudinary.com/startup-grind/image/upload/c_fill,w_500,h_500,g_center/c_fill,dpr_2.0,f_auto,g_center,q_auto:good/v1/gcs/platform-data-atlassian/events/Apt-website-icon-confluence_yV6KChd.png",
            is_react=False,
            project=user_project,
        )
        codemie_onboarding = PrebuiltAssistant(
            name="CodeMie FAQ",
            slug="template-faq-assistant",
            description="""
            This is smart CodeMie assistant which can help you with onboarding process.
            CodeMie can answer to all you questions about capabilities, usage and so on.
            """.strip(),
            system_prompt=CODEMIE_ONBOARDING_PROMPT,
            is_react=False,
            toolkits=[],
            icon_url="https://epam-gen-ai-run.github.io/ai-run-install/docs/assets/ai/ai-run-codemie-new.png",
            project="codemie",
        )
        codemie_jira_bugs = PrebuiltAssistant(
            name="CodeMie Feedback",
            slug="template-feedback-assistant",
            description="""
            CodeMie assistant to report bugs and improvements into CodeMie Jira.
            """.strip(),
            system_prompt=CODEMIE_JIRA_PROMPT,
            toolkits=cast_to_toolkit_details(
                [
                    ProjectManagementToolkitUI(
                        tools=[
                            Tool.from_metadata(GENERIC_JIRA_TOOL),
                        ]
                    ),
                ]
            ),
            is_react=False,
            icon_url="https://epam-gen-ai-run.github.io/ai-run-install/docs/assets/ai/ai-run-codemie-new.png",
            project="codemie",
        )
        codemie_backend_local_tester = PrebuiltAssistant(
            name="CodeMie Back-end Local Unit Tester",
            slug="template-back-end-local-unit-tester-assistant",
            description="""
            Unit Test Assistant for CodeMie. This assistant can help you to test your code locally.
            """.strip(),
            system_prompt=LOCAL_UNIT_TEST_PROMPT,
            toolkits=cast_to_toolkit_details(
                [
                    FileSystemToolkitUI(
                        tools=[
                            Tool.from_metadata(READ_FILE_TOOL),
                            Tool.from_metadata(WRITE_FILE_TOOL),
                            Tool.from_metadata(LIST_DIRECTORY_TOOL),
                            Tool.from_metadata(COMMAND_LINE_TOOL),
                        ]
                    ),
                ]
            ),
            is_react=False,
            icon_url=QA_TESTER_ICON_URL,
            project=user_project,
        )
        codemie_frontend_local_tester = PrebuiltAssistant(
            name="CodeMie Front-end Local Unit Tester",
            slug="template-front-end-local-unit-tester-assistant",
            description="""
            Unit Test Assistant for CodeMie. This assistant can help you to test your code locally.
            """.strip(),
            system_prompt=LOCAL_FE_UNIT_TEST_PROMPT,
            toolkits=cast_to_toolkit_details(
                [
                    FileSystemToolkitUI(
                        tools=[
                            Tool.from_metadata(READ_FILE_TOOL),
                            Tool.from_metadata(WRITE_FILE_TOOL),
                            Tool.from_metadata(LIST_DIRECTORY_TOOL),
                            Tool.from_metadata(COMMAND_LINE_TOOL),
                        ]
                    ),
                ]
            ),
            is_react=False,
            icon_url=QA_TESTER_ICON_URL,
            project=user_project,
        )
        codemie_local_ui_developer = PrebuiltAssistant(
            name="CodeMie UI Local Developer",
            slug="template-ui-local-developer-assistant",
            description="""
            CodeMie UI Local Developer Assistant. This assistant can help you to develop your UI locally.
            """.strip(),
            system_prompt=CODEMIE_LOCAL_UI_DEVELOPMENT_PROMPT,
            toolkits=cast_to_toolkit_details(
                [
                    FileSystemToolkitUI(
                        tools=[
                            Tool.from_metadata(READ_FILE_TOOL),
                            Tool.from_metadata(WRITE_FILE_TOOL),
                            Tool.from_metadata(LIST_DIRECTORY_TOOL),
                            Tool.from_metadata(COMMAND_LINE_TOOL),
                        ]
                    ),
                ]
            ),
            is_react=False,
            icon_url=QA_TESTER_ICON_URL,
            project=user_project,
        )
        codemie_local_backend_developer = PrebuiltAssistant(
            name="CodeMie Back-end Local Developer",
            slug="template-back-end-local-developer-assistant",
            description="""
            CodeMie Back-end Local Developer Assistant. This assistant can help you to develop your back-end locally.
            """.strip(),
            system_prompt=CODEMIE_LOCAL_BACKEND_DEVELOPMENT_PROMPT,
            toolkits=cast_to_toolkit_details(
                [
                    FileSystemToolkitUI(
                        tools=[
                            Tool.from_metadata(READ_FILE_TOOL),
                            Tool.from_metadata(WRITE_FILE_TOOL),
                            Tool.from_metadata(LIST_DIRECTORY_TOOL),
                            Tool.from_metadata(COMMAND_LINE_TOOL),
                        ]
                    ),
                ]
            ),
            is_react=False,
            icon_url=DEVELOPER_ICON_URL,
            project=user_project,
        )
        qa_checklist_assistant = PrebuiltAssistant(
            name="QA Checklist Assistant",
            slug="template-qa-checklist-assistant",
            description="""
            Checklist generator for QA activities.
            """.strip(),
            system_prompt=QA_CHECKLIST_GENERATOR,
            toolkits=cast_to_toolkit_details(
                [
                    ProjectManagementToolkitUI(
                        tools=[
                            Tool.from_metadata(GENERIC_JIRA_TOOL),
                            Tool.from_metadata(GENERIC_CONFLUENCE_TOOL),
                        ]
                    ),
                ]
            ),
            is_react=False,
            icon_url="https://epam-gen-ai-run.github.io/ai-run-install/docs/assets/ai/QAChecklistGenerator.png",
            project=user_project,
        )
        qa_test_case_generator = PrebuiltAssistant(
            name="[Template] QA Test Case Assistant",
            slug="template-qa-test-case-assistant",
            description="""
            Test case generator for QA activities.
            """.strip(),
            system_prompt=QA_TESTCASES_GENERATOR,
            toolkits=cast_to_toolkit_details(
                [
                    ProjectManagementToolkitUI(
                        tools=[
                            Tool.from_metadata(GENERIC_JIRA_TOOL),
                            Tool.from_metadata(GENERIC_CONFLUENCE_TOOL),
                        ]
                    ),
                ]
            ),
            is_react=False,
            icon_url="https://epam-gen-ai-run.github.io/ai-run-install/docs/assets/ai/QATestCaseGenerator.png",
            project=user_project,
        )
        aqa_test_case_generator_with_backend_code = PrebuiltAssistant(
            name="CodeMie AQA Test Case Assistant (With BE Code)",
            slug="template-aqa-test-case-with-be-code-assistant",
            description="""
            CodeMie AQA Test Case Assistant which can analyze backend application in order to write test cases in autotest repository.
            """.strip(),
            llm_model_type=llm_service.default_llm_model,
            system_prompt=AQA_TESTCASES_GENERATOR_WITH_BACKEND_CODE,
            toolkits=cast_to_toolkit_details(
                [
                    GitToolkitUI(
                        tools=[
                            Tool.from_metadata(SET_ACTIVE_BRANCH_TOOL),
                            Tool.from_metadata(UPDATE_FILE_TOOL),
                            Tool.from_metadata(CREATE_PULL_REQUEST_TOOL),
                            Tool.from_metadata(CREATE_GIT_BRANCH_TOOL),
                            Tool.from_metadata(LIST_BRANCHES_TOOL),
                            Tool.from_metadata(CREATE_FILE_TOOL),
                            Tool.from_metadata(DELETE_FILE_TOOL),
                        ]
                    ),
                    VcsToolkitUI(tools=[Tool.from_metadata(GITLAB_TOOL)]),
                ]
            ),
            is_react=False,
            icon_url="https://epam-gen-ai-run.github.io/ai-run-install/docs/assets/ai/AQAAPIAssistant.png",
            project=user_project,
        )
        aqa_test_case_generator_with_open_api_spec = PrebuiltAssistant(
            name="CodeMie AQA Test Case Assistant (With OpenAPI Spec.)",
            slug="template-aqa-test-case-with-openapi-spec-assistant",
            description="""
            CodeMie AQA Test Case Assistant which can analyze OpenAPI specification in order to write test cases in autotest repository.
            """.strip(),
            llm_model_type=llm_service.default_llm_model,
            system_prompt=AQA_TESTCASES_GENERATOR_WITH_BACKEND_CODE,
            toolkits=cast_to_toolkit_details(
                [
                    GitToolkitUI(
                        tools=[
                            Tool.from_metadata(SET_ACTIVE_BRANCH_TOOL),
                            Tool.from_metadata(UPDATE_FILE_TOOL),
                            Tool.from_metadata(CREATE_PULL_REQUEST_TOOL),
                            Tool.from_metadata(CREATE_GIT_BRANCH_TOOL),
                            Tool.from_metadata(LIST_BRANCHES_TOOL),
                            Tool.from_metadata(CREATE_FILE_TOOL),
                            Tool.from_metadata(DELETE_FILE_TOOL),
                        ]
                    ),
                    VcsToolkitUI(tools=[Tool.from_metadata(GITLAB_TOOL)]),
                ]
            ),
            is_react=False,
            icon_url="https://epam-gen-ai-run.github.io/ai-run-install/docs/assets/ai/AQAAPIAssistant.png",
            project=user_project,
        )
        aqa_test_case_generator_ui_tests = PrebuiltAssistant(
            name="CodeMie AQA UI Automation Test Creator",
            slug="template-aqa-ui-automation-test-creator-assistant",
            description="""
            This assistant is created for complex solutions for building UI automation tests. It took context from the repository and continue to cover other test cases.
            """.strip(),
            llm_model_type=llm_service.default_llm_model,
            system_prompt=AQA_TESTCASES_GENERATOR_UI_TESTS,
            toolkits=cast_to_toolkit_details(
                [
                    GitToolkitUI(
                        tools=[
                            Tool.from_metadata(SET_ACTIVE_BRANCH_TOOL),
                            Tool.from_metadata(UPDATE_FILE_TOOL),
                            Tool.from_metadata(CREATE_PULL_REQUEST_TOOL),
                            Tool.from_metadata(CREATE_GIT_BRANCH_TOOL),
                            Tool.from_metadata(LIST_BRANCHES_TOOL),
                            Tool.from_metadata(CREATE_FILE_TOOL),
                            Tool.from_metadata(DELETE_FILE_TOOL),
                        ]
                    ),
                    VcsToolkitUI(tools=[Tool.from_metadata(GITLAB_TOOL)]),
                    ProjectManagementToolkitUI(tools=[Tool.from_metadata(GENERIC_JIRA_TOOL)]),
                ]
            ),
            is_react=False,
            icon_url="https://epam-gen-ai-run.github.io/ai-run-install/docs/assets/ai/AQAUiTestGenerator.png",
            project=user_project,
        )
        aqa_ac_bdd_tests = PrebuiltAssistant(
            name="CodeMie Test Automation Based On AC",
            slug="template-test-automation-based-on-ac-assistant",
            description="""
            This assistant is aimed to create solutions converting acceptance criteria from ticket to BDD autotest scenarios.
            """.strip(),
            llm_model_type=llm_service.default_llm_model,
            system_prompt=AQA_AC_BDD_GENERATOR_UI_TESTS,
            toolkits=cast_to_toolkit_details(
                [
                    GitToolkitUI(
                        tools=[
                            Tool.from_metadata(SET_ACTIVE_BRANCH_TOOL),
                            Tool.from_metadata(UPDATE_FILE_TOOL),
                            Tool.from_metadata(CREATE_PULL_REQUEST_TOOL),
                            Tool.from_metadata(CREATE_GIT_BRANCH_TOOL),
                            Tool.from_metadata(LIST_BRANCHES_TOOL),
                            Tool.from_metadata(CREATE_FILE_TOOL),
                            Tool.from_metadata(DELETE_FILE_TOOL),
                        ]
                    ),
                    VcsToolkitUI(
                        tools=[
                            Tool.from_metadata(GITLAB_TOOL),
                        ]
                    ),
                    ProjectManagementToolkitUI(
                        tools=[
                            Tool.from_metadata(GENERIC_JIRA_TOOL),
                        ]
                    ),
                ]
            ),
            is_react=False,
            icon_url="https://epam-gen-ai-run.github.io/ai-run-install/docs/assets/ai/ACAQABDDAgent.png",
            project=user_project,
        )
        pull_request_file_analyzer = PrebuiltAssistant(
            name="[Template] Pull Request File Analyzer",
            slug="template-pull-request-file-analyzer-assistant",
            description="""
              This assistant is triggered either by a webhook or a user message with a pull request URL.
              It fetches the list of changed files, and outputs the final list of modified files.
            """.strip(),
            llm_model_type=llm_service.default_llm_model,
            system_prompt=PULL_REQUEST_FILE_PROMPT,
            toolkits=cast_to_toolkit_details(
                [
                    GitToolkitUI(
                        tools=[
                            Tool.from_metadata(GET_PR_CHANGES),
                        ]
                    )
                ]
            ),
            is_react=False,
            icon_url=CODE_REVIEW_PNG_ICON,
            project=user_project,
        )
        pull_request_reviewer = PrebuiltAssistant(
            name="[Template] Python Reviewer",
            slug="template-python-reviewer-assistant",
            description="""
                This assistant is responsible for analyzing a given file diff from pull request to ensure added or modified code
                adheres to Python's best practices and highest performance standards. It fetches the diff for a given file path,
                reviews only added or modified lines, and provides constructive comments where improvements are needed.
            """.strip(),
            llm_model_type=llm_service.default_llm_model,
            system_prompt=PULL_REQUEST_REVIEWER_PROMPT,
            toolkits=cast_to_toolkit_details(
                [
                    GitToolkitUI(
                        tools=[
                            Tool.from_metadata(GET_PR_CHANGES),
                        ]
                    ),
                ]
            ),
            is_react=False,
            icon_url=CODE_REVIEW_PNG_ICON,
            project=user_project,
        )
        pull_request_review_summarizer = PrebuiltAssistant(
            name="[Template] Python Reviewer Summarizer",
            slug="template-python-reviewer-summarizer-assistant",
            description="""
                  This assistant is designed to streamline the code review process by intelligently analyzing, prioritizing,
                  and managing feedback generated from initial code reviews.
                  Its primary role is to process a collection of preliminary code review comments, identify critical issues,
                  reduce redundancy, and ensure that the most valuable and actionable feedback is directly posted on pull requests.
                  By aggregating similar issues, prioritizing comments based on severity, and minimizing noise,
                  the assistant enhances the effectiveness of code reviews, making them more focused and useful for developers.
                  Its end goal is to facilitate a more efficient review process, helping developers to quickly address
                  the most important changes without being overwhelmed by excessive commentary.
            """.strip(),
            llm_model_type=llm_service.default_llm_model,
            system_prompt=PULL_REVIEWER_SUMMARIZER_PROMPT,
            toolkits=cast_to_toolkit_details(
                [
                    GitToolkitUI(
                        tools=[
                            Tool.from_metadata(GET_PR_CHANGES),
                            Tool.from_metadata(CREATE_PR_CHANGE_COMMENT),
                        ]
                    )
                ]
            ),
            is_react=False,
            icon_url=CODE_REVIEW_PNG_ICON,
            project=user_project,
        )
        csv_analytic = PrebuiltAssistant(
            name="[Template] CSV Analyst",
            slug="template-csv-analyst-assistant",
            description="This assistant can help you to analyze and get data from attached CSV files.",
            system_prompt=CSV_ANALYST_PROMPT,
            is_react=False,
            icon_url="https://ucarecdn.com/9cd42af5-7a6d-438c-ba44-bfda186711de/OnlineImageEditor.png",
            toolkits=[],
            project=user_project,
        )

        local_plugin_developer = PrebuiltAssistant(
            name="Local Developer via Plugin Engine",
            slug="template-developer-via-plugin-engine-assistant",
            description="""
            Developer who can implement changes on local machine via CodeMie Plugin Engine.
            """.strip(),
            system_prompt=LOCAL_PLUGIN_DEVELOPER_PROMPT,
            toolkits=cast_to_toolkit_details([get_plugin_toolkit_ui_info()]),
            is_react=False,
            icon_url=DEVELOPER_ICON_URL,
            project=user_project,
        )

        release_manager_assistant = PrebuiltAssistant(
            name="[Template] Release Manager Assistant",
            slug="template-release-manager-assistant",
            description="""
            Release Manager Assistant. Main role is to support user in release process, generate release notes, close necessary tickets in Jira, create releases in Jira
            """.strip(),
            llm_model_type=llm_service.default_llm_model,
            system_prompt=RELEASE_MANAGER_PROMPT,
            toolkits=cast_to_toolkit_details(
                [
                    GitToolkitUI(
                        tools=[
                            Tool.from_metadata(SET_ACTIVE_BRANCH_TOOL),
                            Tool.from_metadata(UPDATE_FILE_TOOL),
                            Tool.from_metadata(CREATE_PULL_REQUEST_TOOL),
                            Tool.from_metadata(CREATE_GIT_BRANCH_TOOL),
                            Tool.from_metadata(LIST_BRANCHES_TOOL),
                        ]
                    ),
                    VcsToolkitUI(
                        tools=[
                            Tool.from_metadata(GITLAB_TOOL),
                        ]
                    ),
                    ProjectManagementToolkitUI(
                        tools=[
                            Tool.from_metadata(GENERIC_JIRA_TOOL),
                        ]
                    ),
                ]
            ),
            is_react=False,
            icon_url="https://epam-gen-ai-run.github.io/ai-run-install/docs/assets/ai/BA_Bettie.png",
            project=user_project,
        )
        gitlab_ci_assistant = PrebuiltAssistant(
            name="[Template] GitLab CI/CD Assistant",
            slug="template-gitlab-cicd-assistant",
            description="""Prebuilt GitLab CI Assistant. Main role is to help with generating YAML configuration for GitLab CI/CD.""",
            system_prompt=GITLAB_CI_AGENT_PROMPT,
            toolkits=cast_to_toolkit_details([get_plugin_toolkit_ui_info()]),
            icon_url="https://images.ctfassets.net/xz1dnu24egyd/1IRkfXmxo8VP2RAE5jiS1Q/ea2086675d87911b0ce2d34c354b3711/gitlab-logo-500.png",
            is_react=False,
            project=user_project,
        )
        sonar_issues_retriever = PrebuiltAssistant(
            name="[Template] Sonar Issues Retriever",
            slug="template-sonar-issues-retriever-assistant",
            description="""
            CodeMie Sonar Assistant. This assistant can help you to retrieve sonar scanner results and give descriptive and readable output.
            """.strip(),
            llm_model_type=llm_service.default_llm_model,
            system_prompt=SONAR_RETRIEVER,
            toolkits=cast_to_toolkit_details(
                [
                    CodeToolkitUI(
                        tools=[
                            Tool.from_metadata(SONAR_TOOL),
                        ]
                    ),
                ]
            ),
            is_react=False,
            icon_url="https://epam-gen-ai-run.github.io/ai-run-install/docs/assets/ai/QAChecklistGenerator.png",
            project=user_project,
        )

        base_list = [
            jira_assistant,
            release_manager_assistant,
            python_developer_assistant,
            frontend_vue_developer_assistant,
            design_to_code_assistant,
            code_reviewer,
            researcher,
            chatgpt,
            onboarding_assistant,
            documentation_assistant,
            notifications_assistant,
            newsletter_assistant,
            cloud_assistant,
            confluence_assistant,
            csv_analytic,
            local_plugin_developer,
            sonar_issues_retriever,
            qa_checklist_assistant,
            qa_test_case_generator,
            aqa_test_case_generator_with_backend_code,
            aqa_test_case_generator_with_open_api_spec,
            aqa_test_case_generator_ui_tests,
            gitlab_ci_assistant,
            aqa_ac_bdd_tests,
            pull_request_file_analyzer,
            pull_request_reviewer,
            pull_request_review_summarizer,
        ]
        if user.is_admin:
            base_list.append(codemie_onboarding)
            base_list.append(codemie_jira_bugs)
            base_list.append(codemie_backend_local_tester)
            base_list.append(codemie_frontend_local_tester)
            base_list.append(codemie_local_ui_developer)
            base_list.append(codemie_local_backend_developer)

        return base_list
