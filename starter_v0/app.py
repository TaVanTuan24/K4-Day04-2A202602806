from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

from chat import now_iso, run_model_tool_loop, safe_slug, write_transcript
from env_loader import load_lab_env
from providers import make_provider
from tools import load_tool_declarations, to_openai_tools
from versioning import artifact_version_dict, build_artifact_version

ROOT = Path(__file__).parent
ARTIFACTS_DIR = ROOT / "artifacts"
TRANSCRIPTS_DIR = ROOT / "transcripts"
load_lab_env(ROOT)


def build_transcript(artifact_version, provider_name, model, turn_records) -> dict[str, Any]:
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    transcript_id = "_".join(["ui", safe_slug(provider_name), timestamp])
    return {
        "transcript_id": transcript_id,
        **artifact_version_dict(artifact_version),
        "provider": provider_name,
        "model": model,
        "system_prompt": "artifacts/system_prompt.md",
        "tools": "artifacts/tools.yaml",
        "history_window": 5,
        "max_tool_rounds": 4,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "turns": turn_records,
    }


def main() -> None:
    st.set_page_config(page_title="IT Helpdesk Agent", layout="wide")
    st.title("IT Helpdesk Agent")

    provider_name = st.sidebar.selectbox("Provider", ["openrouter", "openai", "anthropic", "gemini"])
    version = st.sidebar.text_input("Artifact version", "v3")
    model_override = st.sidebar.text_input("Model override (optional)", "")

    system_prompt_path = ARTIFACTS_DIR / "system_prompt.md"
    tools_path = ARTIFACTS_DIR / "tools.yaml"
    system_prompt = system_prompt_path.read_text(encoding="utf-8")
    tool_declarations = load_tool_declarations(tools_path)
    openai_tools = to_openai_tools(tool_declarations)
    artifact_version = build_artifact_version(version, system_prompt_path, tools_path)

    provider = make_provider(provider_name)
    model = model_override or getattr(provider, "default_model", None)

    st.sidebar.subheader("Artifact version")
    st.sidebar.code(f"{artifact_version.artifact_version}\nprompt: {artifact_version.prompt_hash[:12]}\ntools : {artifact_version.tools_hash[:12]}", language=None)

    if "history" not in st.session_state:
        st.session_state.history = []
    if "turn_records" not in st.session_state:
        st.session_state.turn_records = []

    user_text = st.chat_input("Nhập yêu cầu hỗ trợ IT (ví dụ: Kiểm tra VPN trên máy, kèm asset ID)")

    if user_text:
        messages = [
            {"role": "system", "content": system_prompt},
            *st.session_state.history,
            {"role": "user", "content": user_text},
        ]
        with st.spinner("Đang xử lý…"):
            result = run_model_tool_loop(
                provider=provider,
                messages=messages,
                tools=openai_tools,
                model=model,
                max_tool_rounds=4,
            )

        with st.chat_message("user"):
            st.write(user_text)

        turn_record: dict[str, Any] = {
            "turn_index": len(st.session_state.turn_records) + 1,
            "started_at": now_iso(),
            "user": user_text,
            "status": result.get("status"),
            "assistant_text": result.get("assistant_text"),
            "rounds": result.get("rounds", []),
            "tool_events": result.get("tool_events", []),
            "ended_at": now_iso(),
        }
        st.session_state.turn_records.append(turn_record)

        with st.chat_message("assistant"):
            for round_item in result.get("rounds", []):
                for call in round_item.get("tool_calls", []):
                    with st.expander(f"🔧 {call['name']}", expanded=True):
                        st.json(call.get("args", {}))
                for event in round_item.get("tool_results", []):
                    if event.get("result", {}).get("error"):
                        st.error(f"Tool error: {event['tool']} → {event['result']}")
            has_tool_calls = any(round_item.get("tool_calls") for round_item in result.get("rounds", []))
            if has_tool_calls:
                st.caption("Tool results are shown above the final answer.")
            st.write(result.get("assistant_text"))

        st.session_state.history.append({"role": "user", "content": user_text})
        st.session_state.history.append({"role": "assistant", "content": result.get("assistant_text") or ""})

        transcript = build_transcript(artifact_version, provider_name, model, st.session_state.turn_records)
        transcript_path = TRANSCRIPTS_DIR / f"{transcript['transcript_id']}.transcript.json"
        write_transcript(transcript_path, transcript)
        st.caption(f"Transcript: {transcript_path}")

    with st.sidebar:
        st.subheader("Session transcript")
        if st.session_state.turn_records:
            for rec in st.session_state.turn_records:
                tools = " | ".join(
                    f"{event.get('tool')}" for event in rec.get("tool_events", [])
                )
                st.write(f"- **Turn {rec['turn_index']}** ({rec['status']}): `{tools or 'no tool'}`")


if __name__ == "__main__":
    main()