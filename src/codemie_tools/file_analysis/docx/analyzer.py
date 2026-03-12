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
from typing import Optional, List

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from codemie_tools.file_analysis.docx.exceptions import AnalysisError, InsufficientContentError
from codemie_tools.file_analysis.docx.models import (
    DocumentContent,
    DocumentStructure,
    AnalysisResult,
    ImageAnalysis,
    StructureAnalysis,
    ImageData,
)

logger = logging.getLogger(__name__)


class DocxAnalyzer:
    """
    Analyzer for DOCX documents.

    Provides methods for analyzing document content, text, images, and structure.
    """

    def __init__(self, chat_model: Optional[BaseChatModel] = None):
        """
        Initialize the DocxAnalyzer with dependencies.

        Args:
            chat_model: LangChain chat model for AI-powered analysis
        """
        self.chat_model = chat_model

    def analyze_content(self, content: DocumentContent, instructions: Optional[str] = None) -> AnalysisResult:
        """
        Perform comprehensive analysis on document content.

        Args:
            content: DocumentContent object to analyze
            instructions: Optional specific instructions to guide the analysis

        Returns:
            AnalysisResult with insights and metadata

        Raises:
            AnalysisError: If the analysis fails
            InsufficientContentError: If the document content is insufficient for analysis
        """
        logger.info(f"Performing comprehensive document analysis with instructions: {instructions}")

        if not content or not content.text.strip():
            err_msg = "Document is empty or has no text content"
            logger.error(err_msg)
            raise InsufficientContentError(err_msg)

        try:
            # Analyze text
            text_analysis = self.analyze_text(content.text, instructions=instructions)

            # Analyze images
            image_analysis = None
            if content.images:
                image_analysis = self.analyze_images(content.images, instructions=instructions).image_analysis

            # Analyze structure
            structure_analysis = None
            if hasattr(content, 'structure'):
                structure_analysis = self.analyze_structure(
                    content.structure, instructions=instructions
                ).structure_analysis

            # Combine all analysis results
            return AnalysisResult(
                summary=text_analysis.summary,
                key_topics=text_analysis.key_topics,
                sentiment=text_analysis.sentiment,
                language=text_analysis.language,
                readability_score=text_analysis.readability_score,
                image_analysis=image_analysis,
                structure_analysis=structure_analysis,
            )
        except Exception as e:
            logger.error(f"Error during content analysis: {str(e)}")
            raise AnalysisError(f"Failed to analyze document content: {str(e)}") from e

    def analyze_text(self, text: str, instructions: Optional[str] = None) -> AnalysisResult:
        """
        Analyze document text.

        Args:
            text: Document text content
            instructions: Optional specific instructions to guide the analysis

        Returns:
            AnalysisResult focusing on text analysis

        Raises:
            AnalysisError: If the analysis fails
            InsufficientContentError: If the text is insufficient for analysis
        """
        if not text or not text.strip():
            err_msg = "No text content to analyze"
            logger.error(err_msg)
            raise InsufficientContentError(err_msg)

        logger.info(f"Analyzing document text ({len(text)} characters) with instructions: {instructions}")

        try:
            # Determine if we can use LLM or need fallback
            if self.chat_model:
                return self._analyze_text_with_llm(text, instructions)
            else:
                return self._analyze_text_without_llm(text)
        except Exception as e:
            logger.error(f"Error during text analysis: {str(e)}")
            raise AnalysisError(f"Failed to analyze text: {str(e)}") from e

    def analyze_images(self, images: List[ImageData], instructions: Optional[str] = None) -> AnalysisResult:
        """
        Analyze document images.

        Args:
            images: List of ImageData objects to analyze
            instructions: Optional specific instructions to guide the image analysis

        Returns:
            AnalysisResult focusing on image analysis

        Raises:
            AnalysisError: If the analysis fails
        """
        if not images:
            logger.warning("No images to analyze")
            return AnalysisResult(
                summary="No images found in the document.",
                image_analysis=ImageAnalysis(count=0, types=[], described_content=[]),
            )

        logger.info(f"Analyzing {len(images)} document images")

        try:
            # Extract image types
            img_types = set()
            for img in images:
                img_types.add(img.format.lower() if img.format else "unknown")

            # Extract image OCR text if available
            described_content = []
            for img in images:
                if img.text_content:
                    described_content.append(img.text_content)

            # Create image analysis object
            image_analysis = ImageAnalysis(
                count=len(images), types=list(img_types), described_content=described_content
            )

            # Create result
            summary = f"The document contains {len(images)} images "
            if len(img_types) > 0:
                summary += f"of type(s): {', '.join(img_types)}. "
            if described_content:
                summary += f"Text was extracted from {len(described_content)} image(s)."
            else:
                summary += "No text was extracted from images."

            return AnalysisResult(summary=summary, image_analysis=image_analysis)
        except Exception as e:
            logger.error(f"Error during image analysis: {str(e)}")
            raise AnalysisError(f"Failed to analyze images: {str(e)}") from e

    def analyze_structure(self, structure: DocumentStructure, instructions: Optional[str] = None) -> AnalysisResult:
        """
        Analyze document structure.

        Args:
            structure: DocumentStructure object to analyze
            instructions: Optional specific instructions to guide the structure analysis

        Returns:
            AnalysisResult focusing on structure analysis

        Raises:
            AnalysisError: If the analysis fails
        """
        logger.info(f"Analyzing document structure with instructions: {instructions}")

        try:
            # Count sections, headings and tables
            section_count = len(structure.sections) if structure.sections else 0
            heading_levels = len({h.level for h in structure.headers}) if structure.headers else 0
            table_count = 0  # This would need to be passed separately

            # Calculate complexity score based on structure
            complexity_factors = [
                len(structure.headers) * 0.2,
                len(structure.paragraphs) * 0.05,
                heading_levels * 1.0,
                section_count * 0.7,
                table_count * 0.5,
            ]
            complexity_score = min(10.0, sum(complexity_factors))

            # Create structure analysis object
            structure_analysis = StructureAnalysis(
                sections=section_count,
                heading_levels=heading_levels,
                table_count=table_count,
                complexity_score=complexity_score,
            )

            # Create summary
            summary = f"Document has {section_count} sections with {heading_levels} heading levels. "
            summary += f"The document structure complexity score is {complexity_score:.1f}/10."

            return AnalysisResult(summary=summary, structure_analysis=structure_analysis)
        except Exception as e:
            logger.error(f"Error during structure analysis: {str(e)}")
            raise AnalysisError(f"Failed to analyze document structure: {str(e)}") from e

    def _analyze_text_with_llm(self, text: str, instructions: Optional[str] = None) -> AnalysisResult:
        """
        Analyze text content using LLM.

        Args:
            text: Document text content
            instructions: Optional specific instructions to guide the analysis

        Returns:
            AnalysisResult with LLM-based analysis
        """
        # Limit text length for LLM
        max_chars = min(len(text), 100000)  # Approximate token limit for most models
        truncated_text = text[:max_chars]

        # Create prompt for the LLM
        instruction_text = f"\nAdditional instructions: {instructions}" if instructions else ""
        prompt = f"""
        Analyze the following document text and provide:
        1. A concise summary (3-5 sentences)
        2. Key topics (5-7 topics)
        3. Overall sentiment (positive, negative, or neutral)
        4. Language of the text
        5. Readability score (1-10, where 10 is very readable)
        {instruction_text}

        Format the response as a JSON object with fields: summary, key_topics, sentiment, language, readability_score

        Document text:
        {truncated_text}
        """.strip()

        try:
            # Get response from LLM
            messages = [HumanMessage(content=prompt)]
            llm_result = self.chat_model.invoke(messages).content

            # Parse response
            # Note: In a real implementation, using a proper JSON parser would be better
            # This is simplified for the example
            lines = llm_result.split('\n')

            # Extract fields
            summary_line = next((line for line in lines if '"summary"' in line), '')
            summary = summary_line.split(':', 1)[1].strip().strip('",')

            key_topics_line = next((line for line in lines if '"key_topics"' in line), '')
            key_topics_str = key_topics_line.split(':', 1)[1].strip().strip('",[]')
            key_topics = [topic.strip().strip('"\'') for topic in key_topics_str.split(',')]

            sentiment_line = next((line for line in lines if '"sentiment"' in line), '')
            sentiment = sentiment_line.split(':', 1)[1].strip().strip('",')

            language_line = next((line for line in lines if '"language"' in line), '')
            language = language_line.split(':', 1)[1].strip().strip('",')

            readability_line = next((line for line in lines if '"readability_score"' in line), '')
            readability = float(readability_line.split(':', 1)[1].strip().strip('",'))

            return AnalysisResult(
                summary=summary,
                key_topics=key_topics,
                sentiment=sentiment,
                language=language,
                readability_score=readability,
            )
        except Exception as e:
            logger.warning(f"Error in LLM analysis: {str(e)}, falling back to basic analysis")
            return self._analyze_text_without_llm(text)

    @staticmethod
    def _analyze_text_without_llm(text: str) -> AnalysisResult:
        """
        Basic text analysis without LLM.

        Args:
            text: Document text content

        Returns:
            AnalysisResult with basic analysis
        """
        # Very basic text analysis
        words = text.split()
        total_words = len(words)
        sentences = text.split('.')
        total_sentences = len(sentences)

        # Generate a simple summary
        if total_sentences > 5:
            summary_sentences = sentences[:3]
            summary = '. '.join(s.strip() for s in summary_sentences) + '.'
        else:
            summary = text[:200] + '...' if len(text) > 200 else text

        # Extract potential key topics based on word frequency
        word_freq = {}
        for word in words:
            word = word.strip().lower()
            if len(word) > 4:  # Only consider words with 5+ characters
                word_freq[word] = word_freq.get(word, 0) + 1

        # Get top words as topics
        key_topics = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:5]
        key_topics = [topic for topic, _ in key_topics]

        # Simple readability measure - average sentence length
        avg_sentence_length = total_words / max(1, total_sentences)
        readability_score = 10 - min(10, abs(avg_sentence_length - 15) / 2)

        return AnalysisResult(
            summary=summary,
            key_topics=key_topics,
            sentiment="neutral",  # Default without sentiment analysis
            language="English",  # Default without language detection
            readability_score=readability_score,
        )
