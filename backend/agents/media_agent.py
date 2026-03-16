"""
agents/media_agent.py — Analyzes audio, video, and image files.

Example queries:
  "What was discussed in the meeting recording?"
  "Transcribe the audio file"
  "What's in this image?"
  "Extract action items from the video call"
  "What decisions were made in meeting.mp3?"

Strategy:
  - For audio/video: retrieve via search (Gemini embedded them at index time),
    then use Gemini multimodal for direct content analysis if file is accessible
  - For images: direct Gemini vision analysis
  - Falls back to snippet-based QA if file is too large or inaccessible
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agents.base import BaseAgent, AgentResult


_SYSTEM = """You are a media analysis assistant.
Analyze audio, video, and image content and provide clear, structured insights.

For meetings/calls:
  - List key topics discussed
  - Extract decisions made
  - List action items with owners if mentioned
  - Note any deadlines

For images:
  - Describe what you see
  - Extract any text visible
  - Note diagrams, charts, or technical content

Be specific and factual."""


_TRANSCRIPT_SYSTEM = """Extract a structured analysis from this meeting/audio content:

**Topics Discussed**
- [topic 1]
- [topic 2]

**Decisions Made**
- [decision 1]

**Action Items**
1. [person]: [task] by [date if mentioned]

**Key Takeaways**
[1-2 sentences]"""


class MediaAgent(BaseAgent):

    name = "media"
    description = "Analyzes audio, video, and image files"
    capabilities = [
        "analyze audio",
        "analyze video",
        "transcribe meeting",
        "what was discussed",
        "extract action items from recording",
        "analyze image",
        "what's in this image",
    ]

    MEDIA_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".mp4", ".mov", ".avi", ".mkv",
                        ".png", ".jpg", ".jpeg", ".webp", ".gif"}

    def run(self, query: str, context: dict[str, Any]) -> AgentResult:
        documents = context.get("documents", [])

        # Filter to media files only
        media_docs = [
            d for d in documents
            if Path(d.get("file_path", "")).suffix.lower() in self.MEDIA_EXTENSIONS
        ]

        # If no media docs in context, search for them
        if not media_docs:
            from search import hybrid_search
            results = hybrid_search(query, top_k=8)
            media_docs = [
                r.to_dict() for r in results
                if Path(r.file_path).suffix.lower() in self.MEDIA_EXTENSIONS
            ]

        # If still none, fall through to text docs as fallback
        if not media_docs:
            if documents:
                # Use whatever we have
                media_docs = documents[:3]
            else:
                return AgentResult(
                    agent_name=self.name,
                    output=(
                        "No media files found. Make sure you've indexed folders containing "
                        "audio (.mp3, .wav), video (.mp4, .mov), or image files (.png, .jpg)."
                    ),
                    sources=[],
                )

        # Try direct multimodal analysis for the first media file
        primary = media_docs[0]
        file_path = primary.get("file_path", "")
        ext = Path(file_path).suffix.lower()

        direct_analysis = self._try_direct_analysis(file_path, ext, query)

        if direct_analysis:
            return AgentResult(
                agent_name=self.name,
                output=direct_analysis,
                sources=media_docs[:3],
                metadata={
                    "analysis_type": "direct_multimodal",
                    "file": primary.get("filename"),
                    "file_type": ext.lstrip("."),
                },
            )

        # Fallback: use snippet context + LLM
        doc_context = self._get_doc_context(media_docs, max_chars=4000)
        is_audio_video = ext in {".mp3", ".wav", ".m4a", ".mp4", ".mov"}

        if is_audio_video:
            system = _TRANSCRIPT_SYSTEM
            prompt = f"""Based on this audio/video file content:

{doc_context}

User question: {query}

Provide a structured analysis:"""
        else:
            system = _SYSTEM
            prompt = f"""Based on this media file:

{doc_context}

User question: {query}

Analyze:"""

        output = self._call_gemini(prompt, system=system, temperature=0.2)

        return AgentResult(
            agent_name=self.name,
            output=output,
            sources=media_docs[:3],
            metadata={
                "analysis_type": "snippet_based",
                "files_analyzed": len(media_docs),
            },
        )

    def _try_direct_analysis(self, file_path: str, ext: str, query: str) -> str | None:
        """
        Attempt direct Gemini multimodal analysis of the file.
        Returns None if file is too large, missing, or unsupported.
        """
        if not file_path:
            return None

        path = Path(file_path)
        if not path.exists():
            return None

        # Size limits: images < 10MB, audio < 15MB, video < 30MB
        size_mb = path.stat().st_size / (1024 * 1024)
        limits = {
            **dict.fromkeys([".png", ".jpg", ".jpeg", ".webp", ".gif"], 10),
            **dict.fromkeys([".mp3", ".wav", ".m4a", ".ogg"], 15),
            **dict.fromkeys([".mp4", ".mov", ".avi", ".mkv"], 30),
        }
        limit = limits.get(ext, 10)
        if size_mb > limit:
            self.logger.info(f"File too large for direct analysis: {size_mb:.1f}MB > {limit}MB")
            return None

        try:
            import os
            from google import genai
            from google.genai import types
            from config import settings

            api_key = settings.gemini_api_key or os.getenv("GEMINI_API_KEY", "")
            client = genai.Client(api_key=api_key)

            mime_map = {
                ".mp3": "audio/mpeg", ".wav": "audio/wav",
                ".m4a": "audio/mp4", ".ogg": "audio/ogg",
                ".mp4": "video/mp4", ".mov": "video/quicktime",
                ".png": "image/png", ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg", ".webp": "image/webp",
                ".gif": "image/gif",
            }
            mime = mime_map.get(ext)
            if not mime:
                return None

            with open(path, "rb") as f:
                raw_bytes = f.read()

            prompt_text = (
                f"Analyze this {ext.lstrip('.')} file and answer: {query}\n\n"
                "Provide a detailed, structured analysis."
            )

            response = client.models.generate_content(
                model=settings.gemini_llm_model,
                contents=[
                    types.Part.from_bytes(data=raw_bytes, mime_type=mime),
                    types.Part(text=prompt_text),
                ],
                config=types.GenerateContentConfig(
                    system_instruction=_SYSTEM,
                    temperature=0.2,
                    max_output_tokens=1024,
                ),
            )
            return response.text

        except Exception as e:
            self.logger.warning(f"Direct media analysis failed: {e}")
            return None