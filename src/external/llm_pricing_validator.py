#!/usr/bin/env python3
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

"""
Simple LLM Pricing Validator - Display Only Version

Compares CodeMie LLM configurations with LiteLLM's official pricing data and
displays a simple table with filename, model, and issues.

Usage:
    python src/external/llm_pricing_validator.py [--config-dir CONFIG_DIR]

Options:
    --config-dir CONFIG_DIR    Specify custom LLM config directory path (default: config/llms)
"""

import requests
import yaml
import sys
import argparse
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
import json


@dataclass
class ModelComparison:
    """Data class for storing model comparison results"""

    config_file: str
    model_name: str
    provider: str
    deployment_name: str
    status: str  # 'match', 'discrepancy', 'missing_fields', 'not_found'
    discrepancies: List[str]
    missing_fields: List[str]
    codemie_cost: Dict[str, float]
    litellm_cost: Dict[str, float]
    litellm_key: str = ""


class LLMPricingValidator:
    """Tool for validating LLM pricing configurations and displaying results"""

    LITELLM_JSON_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"

    def __init__(self, config_dir: str = "config/llms"):
        self.config_dir = Path(config_dir)
        self.litellm_data = {}
        self.codemie_configs = {}
        self.comparisons: List[ModelComparison] = []

    def fetch_litellm_data(self) -> bool:
        """Fetch the latest LiteLLM pricing data from GitHub"""
        try:
            print("📥 Fetching LiteLLM pricing data...")
            response = requests.get(self.LITELLM_JSON_URL, timeout=30)
            response.raise_for_status()
            self.litellm_data = response.json()
            print(f"✅ Successfully fetched data for {len(self.litellm_data)} models")
            return True
        except requests.RequestException as e:
            print(f"❌ Error fetching LiteLLM data: {e}")
            return False
        except json.JSONDecodeError as e:
            print(f"❌ Error parsing LiteLLM JSON data: {e}")
            return False

    def load_codemie_configs(self) -> bool:
        """Load all CodeMie LLM configuration files"""
        try:
            config_files = list(self.config_dir.glob("llm-*.yaml"))
            if not config_files:
                print(f"❌ No LLM config files found in {self.config_dir}")
                return False

            print(f"📂 Loading CodeMie configurations...")
            for config_file in config_files:
                with open(config_file, 'r') as f:
                    config_data = yaml.safe_load(f)
                    self.codemie_configs[config_file.name] = config_data
                    llm_count = len(config_data.get('llm_models', []))
                    emb_count = len(config_data.get('embeddings_models', []))
                    print(f"  • {config_file.name}: {llm_count} LLM models, {emb_count} embedding models")

            return True
        except Exception as e:
            print(f"❌ Error loading CodeMie configs: {e}")
            return False

    def normalize_model_name(self, model_name: str) -> str:
        """Remove codemie- prefix and normalize model names"""
        # Remove codemie- prefix
        if model_name.startswith("codemie-"):
            model_name = model_name[8:]

        # Additional normalizations can be added here
        return model_name

    def get_provider_search_keys(self, model_name: str, provider: str) -> List[str]:
        """Generate possible LiteLLM keys based on model name and provider"""
        # Normalize model name (remove codemie- prefix)
        normalized_name = self.normalize_model_name(model_name)

        search_keys = []

        # Provider-specific prefixes
        provider_prefixes = {
            "azure_openai": ["azure/", ""],
            "openai": [""],
            "anthropic": ["anthropic/", ""],
            "vertex_ai": ["vertex_ai/", "gemini/", ""],
            "bedrock": ["bedrock/", ""],
            "aws": ["bedrock/", ""],
            "gcp": ["vertex_ai/", "gemini/", ""],
        }

        # Get prefixes for this provider
        prefixes = provider_prefixes.get(provider, [""])

        # Model name variants to try
        model_variants = [normalized_name]

        # Add specific normalizations for known model patterns
        normalizations = {
            "anthropic.claude-3-5-sonnet-20240620-v1:0": ["claude-3-5-sonnet-20240620", "claude-3-5-sonnet"],
            "us.anthropic.claude-3-5-sonnet-20241022-v2:0": ["claude-3-5-sonnet-20241022", "claude-3-5-sonnet-v2"],
            "us.anthropic.claude-3-7-sonnet-20250219-v1:0": ["claude-3-7-sonnet-20250219", "claude-3-7"],
            "amazon.titan-embed-text-v2:0": ["amazon.titan-embed-text-v2", "titan-embed-text-v2"],
            "text-embedding-ada-002": ["ada", "text-embedding-ada-002"],
            "gemini-1.5-pro-002": ["gemini-1.5-pro", "gemini-1.5-pro-002"],
            "gemini-2.0-flash-001": ["gemini-2.0-flash-thinking-exp-01-21", "gemini-2.0-flash", "gemini-2.0-flash-001"],
            "text-embedding-005": ["textembedding-gecko@003", "textembedding-gecko", "gecko"],
            "claude-3-5-sonnet-v2": ["claude-3-5-sonnet-20241022", "claude-3-5-sonnet-v2"],
            "claude-3-7-sonnet": ["claude-3-7-sonnet-20250219", "claude-3-7"],
        }

        if normalized_name in normalizations:
            model_variants.extend(normalizations[normalized_name])

        # Generate all combinations of prefixes and model variants
        for prefix in prefixes:
            for variant in model_variants:
                if prefix:
                    search_keys.append(f"{prefix}{variant}")
                else:
                    search_keys.append(variant)

        # Remove duplicates while preserving order
        seen = set()
        return [k for k in search_keys if not (k in seen or seen.add(k))]

    def find_litellm_model(self, model_name: str, deployment_name: str, provider: str) -> Tuple[Optional[Dict], str]:
        """Find matching model in LiteLLM data"""
        # Try deployment_name first, then base_name
        search_names = [name for name in [deployment_name, model_name] if name]

        for search_name in search_names:
            search_keys = self.get_provider_search_keys(search_name, provider)

            for key in search_keys:
                if key in self.litellm_data:
                    return self.litellm_data[key], key

        return None, ""

    def compare_costs(self, litellm_data: Dict, codemie_cost: Dict, percentage_tolerance: float = 1.0) -> List[str]:
        """Compare cost data and return list of discrepancies with exact values

        Uses percentage-based tolerance (default 1.0%) for cost comparisons to avoid
        flagging minor floating point differences.
        Only compares cost-related fields, ignoring other model attributes.

        Returns a list of strings with different formats:
        - Missing fields: <codemie_fieldname>=<value>
        - Discrepancies: <codemie_fieldname>=<targetvalue> (source_value)
        - Other issues: Custom formatted text
        """
        discrepancies = []

        def smart_format(val):
            """Format numbers for better readability"""
            if val is None:
                return "None"
            if val < 0.0001:
                return f"{val:.2e}"
            elif val < 1:
                return f"{val:.8f}".rstrip('0').rstrip('.')
            else:
                return f"{val:.6f}"

        def compare_field(field_name: str, litellm_field: str, codemie_field: str):
            # Only process cost fields (explicitly defined as the cost fields we care about)
            if not any(keyword in litellm_field.lower() for keyword in ['cost', 'price', 'token']):
                return

            litellm_val = litellm_data.get(litellm_field)
            codemie_val = codemie_cost.get(codemie_field)

            # For clarity in the report, indicate when values exist in one system but not the other
            if litellm_val is not None and codemie_val is None:
                # Format for missing fields: <codemie_fieldname>=<value>
                formatted_value = smart_format(
                    float(litellm_val) if isinstance(litellm_val, (int, float, str)) else litellm_val
                )
                discrepancies.append(f"{codemie_field}={formatted_value}")
                return

            if litellm_val is None and codemie_val is not None:
                # Field exists in CodeMie but not in LiteLLM - keep original format
                formatted_value = smart_format(
                    float(codemie_val) if isinstance(codemie_val, (int, float, str)) else codemie_val
                )
                discrepancies.append(f"{codemie_field} Present in CodeMie but missing in LiteLLM ({formatted_value})")
                return

            if litellm_val is not None and codemie_val is not None:
                try:
                    litellm_float = float(litellm_val)
                    codemie_float = float(codemie_val)

                    # Use percentage-based comparison for costs
                    if litellm_float == 0 and codemie_float == 0:
                        # Both zero, no discrepancy
                        return
                    elif litellm_float == 0:
                        # LiteLLM is zero but CodeMie isn't - definite discrepancy
                        discrepancies.append(f"{codemie_field}=0 ({smart_format(codemie_float)})")
                    else:
                        # Calculate percentage difference
                        pct_diff = abs(litellm_float - codemie_float) / litellm_float * 100
                        if pct_diff > percentage_tolerance:
                            # Format: <codemie_fieldname>=<targetvalue> (source_value)
                            discrepancies.append(
                                f"{codemie_field}={smart_format(litellm_float)} ({smart_format(codemie_float)})"
                            )
                except (ValueError, TypeError):
                    # Handle non-numeric values
                    discrepancies.append(f"{codemie_field}={litellm_val} ({codemie_val})")

        # Compare ONLY cost fields
        compare_field("input", "input_cost_per_token", "input")
        compare_field("output", "output_cost_per_token", "output")
        compare_field("input_cost_per_token_batches", "input_cost_per_token_batches", "input_cost_per_token_batches")
        compare_field("output_cost_per_token_batches", "output_cost_per_token_batches", "output_cost_per_token_batches")
        compare_field("cache_read_input_token_cost", "cache_read_input_token_cost", "cache_read_input_token_cost")
        compare_field(
            "cache_creation_input_token_cost", "cache_creation_input_token_cost", "cache_creation_input_token_cost"
        )

        return discrepancies

    def find_missing_fields(self, litellm_data: Dict, codemie_cost: Dict) -> List[str]:
        """Find cost-related fields that exist in LiteLLM but are missing in CodeMie"""
        # Not using this method anymore since we're handling missing fields in compare_costs
        # to avoid duplication in the output
        return []

    def validate_configurations(self) -> List[ModelComparison]:
        """Compare all configurations and return validation results"""
        comparisons = []

        print("🔍 Validating configurations...")

        for config_file, config_data in self.codemie_configs.items():
            # Process LLM models
            for model in config_data.get('llm_models', []):
                comparison = self._process_model(model, config_file, 'llm')
                comparisons.append(comparison)

            # Process embedding models
            for model in config_data.get('embeddings_models', []):
                comparison = self._process_model(model, config_file, 'embedding')
                comparisons.append(comparison)

        self.comparisons = sorted(comparisons, key=lambda x: x.config_file)
        return comparisons

    def _process_model(self, model: Dict, config_file: str, model_type: str) -> ModelComparison:
        """Process a single model and return comparison results"""
        model_name = model.get('base_name', '')
        deployment_name = model.get('deployment_name', '')
        provider = model.get('provider', '')
        codemie_cost = model.get('cost', {})

        # Find matching model in LiteLLM
        litellm_data, litellm_key = self.find_litellm_model(model_name, deployment_name, provider)

        if litellm_data is None:
            return ModelComparison(
                config_file=config_file,
                model_name=model_name,
                provider=provider,
                deployment_name=deployment_name,
                status='not_found',
                discrepancies=[],
                missing_fields=[],
                codemie_cost=codemie_cost,
                litellm_cost={},
                litellm_key="",
            )

        # Compare costs
        discrepancies = self.compare_costs(litellm_data, codemie_cost)
        missing_fields = self.find_missing_fields(litellm_data, codemie_cost)

        # Determine status
        if discrepancies and missing_fields:
            status = 'discrepancy_and_missing'
        elif discrepancies:
            status = 'discrepancy'
        elif missing_fields:
            status = 'missing_fields'
        else:
            status = 'match'

        return ModelComparison(
            config_file=config_file,
            model_name=model_name,
            provider=provider,
            deployment_name=deployment_name,
            status=status,
            discrepancies=discrepancies,
            missing_fields=missing_fields,
            codemie_cost=codemie_cost,
            litellm_cost=litellm_data,
            litellm_key=litellm_key,
        )

    def display_simple_table(self):
        """Display a simple table with filename, model, and issues"""
        if not self.comparisons:
            print("No models to display")
            return

        # Find column widths
        file_width = max(len("Config File"), max(len(comp.config_file) for comp in self.comparisons))
        model_width = max(
            len("Model (Provider)"), max(len(f"{comp.model_name} ({comp.provider})") for comp in self.comparisons)
        )

        # Create table header
        header = f"| {'Config File':<{file_width}} | {'Model (Provider)':<{model_width}} | Issues"
        separator = f"+-{'-' * file_width}-+-{'-' * model_width}-+{'-' * 70}"

        # Print the header
        print()
        print(separator)
        print(header)
        print(separator)

        # Print each row
        for comp in self.comparisons:
            # Format the issue details
            if comp.status == 'match':
                issues = "✅ Perfect match"
                model_provider = f"{comp.model_name} ({comp.provider})"
                print(f"| {comp.config_file:<{file_width}} | {model_provider:<{model_width}} | {issues}")
            elif comp.status == 'not_found':
                issues = "❓ Not found in LiteLLM"
                model_provider = f"{comp.model_name} ({comp.provider})"
                print(f"| {comp.config_file:<{file_width}} | {model_provider:<{model_width}} | {issues}")
            else:
                # First line with basic info
                issue_summary = []
                if comp.discrepancies:
                    issue_summary.append(f"💰 {len(comp.discrepancies)} price discrepancies")
                if comp.missing_fields:
                    issue_summary.append(f"📝 {len(comp.missing_fields)} missing fields")

                model_provider = f"{comp.model_name} ({comp.provider})"
                print(
                    f"| {comp.config_file:<{file_width}} | {model_provider:<{model_width}} | {' | '.join(issue_summary)}"
                )

                # Common padding for indented lines
                padding = f"| {'':<{file_width}} | {'':<{model_width}} | "

                # Group issues by type for better readability
                # First: Missing fields (if any)
                missing_items = [d for d in comp.discrepancies if "=" in d and not "(" in d]
                if missing_items:
                    print(f"{padding}  ↳ ⚠️ Missing fields:")
                    for i, item in enumerate(missing_items):
                        prefix = "     └─ " if i == len(missing_items) - 1 else "     ├─ "
                        print(f"{padding}{prefix}{item}")

                # Second: Price discrepancies
                discrepancies = [d for d in comp.discrepancies if "=" in d and "(" in d]
                if discrepancies:
                    # Add a separator between groups if both exist
                    if missing_items:
                        print(f"{padding}")
                    print(f"{padding}  ↳ 📈 Discrepancies: field=litellm_value (codemie_value):")
                    for i, disc in enumerate(discrepancies):
                        prefix = "     └─ " if i == len(discrepancies) - 1 else "     ├─ "
                        print(f"{padding}{prefix}{disc}")

                # Other issues that don't fit the above categories
                other_issues = [d for d in comp.discrepancies if d not in missing_items and d not in discrepancies]
                if other_issues:
                    # Add a separator if other categories exist
                    if missing_items or discrepancies:
                        print(f"{padding}")
                    print(f"{padding}  ↳ 🔎 Other issues:")
                    for i, issue in enumerate(other_issues):
                        prefix = "     └─ " if i == len(other_issues) - 1 else "     ├─ "
                        print(f"{padding}{prefix}{issue}")

                # Add an empty row for better readability between models with issues
                if comp != self.comparisons[-1]:
                    print(f"{padding}")

        # Print footer
        print(separator)

    def run_validation(self) -> bool:
        """Run the validation process and display table"""
        print("🚀 LLM Pricing Validator")

        # Step 1: Fetch LiteLLM data
        if not self.fetch_litellm_data():
            return False

        # Step 2: Load CodeMie configurations
        if not self.load_codemie_configs():
            return False

        # Step 3: Perform validation
        self.validate_configurations()
        print(f"✅ Validation complete - analyzed {len(self.comparisons)} models")

        # Step 4: Display simple table
        self.display_simple_table()

        return True


def main():
    """Main CLI function"""
    # Set up argument parser
    parser = argparse.ArgumentParser(description='Validate LLM pricing configurations')
    parser.add_argument(
        '--config-dir',
        default='../../config/llms',
        help='Directory containing LLM configuration files (default: config/llms)',
    )
    args = parser.parse_args()

    # Check if the specified directory exists
    config_dir = args.config_dir
    if not Path(config_dir).exists():
        print(f"❌ Error: Configuration directory '{config_dir}' not found")
        print("Please specify a valid config directory with --config-dir or run from project root")
        sys.exit(1)

    validator = LLMPricingValidator(config_dir)

    try:
        success = validator.run_validation()
        if not success:
            sys.exit(1)
    except KeyboardInterrupt:
        print("\n⚠️  Validation interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error during validation: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
