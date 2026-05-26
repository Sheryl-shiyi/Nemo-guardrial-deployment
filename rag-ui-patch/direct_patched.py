# Copyright (c) Meta Platforms, Inc. and affiliates.
# Direct mode implementation with context limits fix
# PATCHED: Added NeMo Guardrails pre-check before vector search

import logging
import traceback
import streamlit as st
from llama_stack_ui.distribution.ui.modules.api import llama_stack_api
from llama_stack_ui.distribution.ui.modules.utils import clean_text, get_vector_db_name

logger = logging.getLogger(__name__)

MAX_NUM_RESULTS = 4
MAX_TOKENS = 1000

NEMO_GUARDRAILS_URL = "http://nemo-guardrails.nemo-guardrails.svc.cluster.local:80"
NEMO_MODEL_NAME = "Gemma-3-27B-BF16-Distributed"

GUARDRAIL_MESSAGES = {
    "check forbidden words": "Prepáčte, nemôžem pomôcť s touto témou. Prosím, preformulujte svoju otázku.",
    "check language": "Prepáčte, tento asistent komunikuje len v slovenčine. Prosím, napíšte svoju otázku po slovensky.",
    "self check input": "Prepáčte, nemôžem odpovedať na túto požiadavku z bezpečnostných dôvodov.",
}
DEFAULT_BLOCK_MESSAGE = "Prepáčte, nemôžem spracovať túto požiadavku."


def guardrail_pre_check(prompt):
    """Call NeMo Guardrails /v1/guardrail/checks before processing.
    Returns (is_blocked, message) tuple."""
    try:
        import httpx
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{NEMO_GUARDRAILS_URL}/v1/guardrail/checks",
                json={
                    "model": NEMO_MODEL_NAME,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            result = resp.json()

        if result.get("status") == "blocked":
            rails_status = result.get("rails_status", {})
            for rail_name, msg in GUARDRAIL_MESSAGES.items():
                rail_info = rails_status.get(rail_name, {})
                if rail_info.get("status") == "blocked":
                    return True, msg
            return True, DEFAULT_BLOCK_MESSAGE

        return False, None
    except Exception as e:
        logger.debug("Guardrail pre-check failed: %s", e)
        return False, None


# ============================================================================
# Direct Mode - Helper Functions
# ============================================================================

def extract_text_from_search_result(result):
    """Extract and clean text content from a search result object."""
    text = None
    if hasattr(result, 'content') and isinstance(result.content, list):
        for content_item in result.content:
            if hasattr(content_item, 'text'):
                text = content_item.text
                break
    elif hasattr(result, 'content') and isinstance(result.content, str):
        text = result.content
    elif isinstance(result, dict) and 'content' in result:
        if isinstance(result['content'], list) and result['content']:
            text = result['content'][0].get('text', '')
        else:
            text = result['content']
    return clean_text(text) if text else None


def search_vector_store_direct(prompt, vector_db_id, vector_db_name, state):
    """Search vector store and extract context for Direct mode."""
    search_results = []
    context_parts = []
    display_results = []

    with state.containers.tool_status:
        st.markdown(f"🛠 :grey[_Searching vector store: {vector_db_name}_]")

    logger.debug("Searching vector store %s with query: %s", vector_db_id, prompt)

    search_response = llama_stack_api.client.vector_stores.search(
        vector_store_id=vector_db_id,
        query=prompt,
        max_num_results=MAX_NUM_RESULTS,
    )

    logger.debug("Search response: %s", search_response)

    if hasattr(search_response, 'data') and search_response.data:
        search_results = search_response.data
    elif hasattr(search_response, 'chunks') and search_response.chunks:
        search_results = search_response.chunks
    elif hasattr(search_response, 'results') and search_response.results:
        search_results = search_response.results

    if search_results:
        for result in search_results:
            text_content = extract_text_from_search_result(result)
            if text_content:
                attrs = getattr(result, 'attributes', {})
                source = attrs.get('source') or getattr(result, 'filename', 'unknown')
                context_parts.append(f"[Source: {source}]: {text_content}")
                display_results.append({"source": source, "text": text_content})

        with state.containers.tool_results:
            with st.expander(f"📄 Search Results from '{vector_db_name}'", expanded=False):
                st.json(display_results)

        logger.debug("Built context with %s documents", len(context_parts))
    else:
        with state.containers.tool_results:
            st.info(f"No results found in '{vector_db_name}'")

    return search_results, context_parts, display_results


def build_rag_messages(prompt, context_parts, system_prompt):
    """Build messages for LLM - with or without RAG context."""
    if context_parts:
        context = "\n\n".join(context_parts)
        extended_prompt = (
            f"Please answer the following query using the context below.\n\n"
            f"CONTEXT:\n{context}\n\n"
            f"QUERY:\n{prompt}"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": extended_prompt}
        ]
        logger.debug(
            "Built RAG prompt with %s documents, total context length: %s",
            len(context_parts), len(context)
        )
    else:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
        logger.debug("No context - using normal chat mode")
    return messages


def stream_completions_direct(completion_response, state):
    """Stream chunks from Completions API and update state."""
    for chunk in completion_response:
        logger.debug("Completion chunk: %s", chunk)
        if hasattr(chunk, 'choices') and len(chunk.choices) > 0:
            delta = chunk.choices[0].delta
            if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                state.update_reasoning(delta.reasoning_content)
            if hasattr(delta, 'content') and delta.content:
                state.update_message(delta.content)


def save_direct_response_to_session(state, all_search_results):
    """Save direct response to session state."""
    state.finalize_reasoning()
    state.finalize_message()

    response_dict = {
        "role": "assistant",
        "content": state.full_response,
        "stop_reason": "end_of_message"
    }

    if state.reasoning_text:
        response_dict["reasoning"] = state.reasoning_text

    if all_search_results:
        db_names = [name for name, _ in all_search_results]
        response_dict["tool_results"] = [
            {
                'title': f"📄 Search Results from '{name}'",
                'type': 'json',
                'content': display
            }
            for name, display in all_search_results
        ]
        response_dict["tool_status"] = (
            f"🛠 :grey[_Searched vector stores: {', '.join(db_names)}_]"
        )

    st.session_state.messages.append(response_dict)


# ============================================================================
# Direct Mode - Main Function (exported)
# ============================================================================

def direct_process_prompt(prompt, state, config):
    """Direct mode: Manual RAG with completions API."""
    context_parts = []
    all_search_results = []

    # Step 0: Guardrails pre-check — block before vector search
    is_blocked, block_message = guardrail_pre_check(prompt)
    if is_blocked:
        with state.containers.tool_status:
            st.markdown("🛡 :red[_Guardrail check: blocked_]")
        state.update_message(block_message)
        state.finalize_message()
        st.session_state.messages.append({
            "role": "assistant",
            "content": block_message,
            "stop_reason": "end_of_message",
            "tool_status": "🛡 :red[_Guardrail check: blocked_]",
        })
        return

    vector_dbs = st.session_state.get("direct_vector_dbs", [])
    if not vector_dbs:
        logger.debug("No vector DB selected - normal chat mode")

    try:
        # Step 1: Search each selected vector store
        for vector_db in vector_dbs:
            vector_db_id = vector_db.id
            vector_db_name = get_vector_db_name(vector_db)
            search_results, parts, display = search_vector_store_direct(
                prompt, vector_db_id, vector_db_name, state
            )
            if search_results:
                all_search_results.append((vector_db_name, display))
            context_parts.extend(parts)

        # Step 2: Build messages (with or without RAG context)
        messages = build_rag_messages(prompt, context_parts, config.system_prompt)

        # Step 3: Call completions API
        logger.debug("Calling completions API with %s messages", len(messages))
        for i, msg in enumerate(messages):
            logger.debug("  Message %s (%s): %s...", i, msg['role'], msg['content'][:200])

        completion_response = llama_stack_api.client.chat.completions.create(
            model=config.model,
            messages=messages,
            temperature=config.sampling.temperature,
            max_tokens=MAX_TOKENS,
            stream=True,
        )

        # Step 4: Stream response and update UI
        stream_completions_direct(completion_response, state)

        # Step 5: Save to session
        save_direct_response_to_session(state, all_search_results)

    except Exception as e:
        st.error(f"Error in Direct mode: {str(e)}")
        logger.debug("Direct mode error: %s", e)
        logger.debug("%s", traceback.format_exc())
