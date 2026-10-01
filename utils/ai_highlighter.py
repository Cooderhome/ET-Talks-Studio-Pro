"""
utils/ai_highlighter.py - Connects to local Ollama API to extract video highlights from a transcript.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"

def read_transcript(transcript_path: str) -> str:
    """Read a VTT or SRT file and return its content."""
    path = Path(transcript_path)
    if not path.exists():
        raise FileNotFoundError(f"Transcript not found: {transcript_path}")
    
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # We could strip timestamps to save tokens, but we NEED the timestamps for the LLM to give us exact cuts.
    return content

def extract_json_from_text(text: str) -> list[dict[str, Any]]:
    """Helper to extract JSON from LLM output."""
    # First, try to just parse the raw string as JSON (since we use structured outputs now, this is highly likely to work)
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "highlights" in data:
            return data["highlights"]
    except json.JSONDecodeError:
        pass

    # Look for a JSON block
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict) and "highlights" in data:
                return data["highlights"]
        except json.JSONDecodeError:
            pass
    
    # Fallback to the old array method just in case
    match = re.search(r"```(?:json)?\s*(\[\s*\{.*?\}\s*\])\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
            
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        elif isinstance(data, dict) and "start_time" in data and "end_time" in data:
            return [data]
    except json.JSONDecodeError:
        pass
        
    # Desperate fallback: find anything that looks like an array
    match = re.search(r"(\[\s*\{.*?\}\s*\])", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
            
    # If we get here, the model hallucinated or failed to output JSON
    raise ValueError("The AI model failed to output a valid JSON array of highlights. This often happens with smaller models like qwen2.5:1.5b. Please try using a larger model like llama3 or mistral.")

def get_highlights_from_ollama(transcript: str, model: str = "llama3") -> list[dict[str, Any]]:
    """
    Send the transcript to Ollama and ask for short video highlights.
    Since large transcripts exceed the context window, we chunk it into smaller pieces.
    Returns a list of dicts: {"start_time": float, "end_time": float, "title": str, "description": str}
    """
    lines = transcript.split("\n")
    # 400 lines is roughly 3000-4000 tokens, very safe for an 8k context window
    chunk_size = 400
    chunks = ["\n".join(lines[i:i+chunk_size]) for i in range(0, len(lines), chunk_size)]
    
    schema = {
        "type": "object",
        "properties": {
            "highlights": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "start_time": {"type": "number"},
                        "end_time": {"type": "number"},
                        "title": {"type": "string"},
                        "description": {"type": "string"}
                    },
                    "required": ["start_time", "end_time", "title", "description"]
                }
            }
        },
        "required": ["highlights"]
    }

    all_highlights = []
    
    for i, chunk in enumerate(chunks):
        logger.info(f"Ollama: Processing transcript chunk {i+1} of {len(chunks)}...")
        prompt = f"""
You are an expert podcast editor and social media manager. Your job is to read the following video transcript chunk (which includes timestamps) and identify the 1 or 2 most viral, engaging, or interesting segments that would make perfect TikTok shorts or YouTube Shorts.

Rules for the clips:
1. Each clip MUST be between 30 and 60 seconds long.
2. Return the start and end times EXACTLY as they appear in the transcript. Provide precise seconds as numbers.
3. Provide a catchy, viral title for each clip.
4. Provide a short description of why this clip works well.

Output Format:
You must output a JSON object containing a 'highlights' array.
Example:
{{
  "highlights": [
    {{
      "start_time": 15.0,
      "end_time": 55.5,
      "title": "The secret to unlimited energy",
      "description": "Explains a controversial health tip clearly."
    }}
  ]
}}

Transcript Chunk:
{chunk}
"""

        data = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": schema
        }
        
        req = urllib.request.Request(
            OLLAMA_URL, 
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        
        try:
            with urllib.request.urlopen(req, timeout=300) as response:
                result = json.loads(response.read().decode("utf-8"))
                response_text = result.get("response", "")
                
                try:
                    chunk_highlights = extract_json_from_text(response_text)
                    all_highlights.extend(chunk_highlights)
                except ValueError as ve:
                    logger.warning(f"Failed to parse JSON for chunk {i+1}: {ve}")
                    continue
        except Exception as e:
            safe_msg = str(e).encode("ascii", "replace").decode("ascii")
            logger.warning(f"Failed to get highlights for chunk {i+1} from Ollama: {safe_msg}")
            continue

    if not all_highlights:
        raise RuntimeError("Ollama API error: Could not extract any valid highlights from the transcript. The model may have hallucinated or failed to format JSON on all chunks.")
        
    return all_highlights
