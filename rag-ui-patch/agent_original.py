# Copyright (c) Meta Platforms, Inc. and affiliates.
# Agent mode implementation with context limits fix

import logging
import streamlit as st
from llama_stack_ui.distribution.ui.modules.api import llama_stack_api
from llama_stack_ui.distribution.ui.modules.utils import clean_text, get_vector_db_name

logger = logging.getLogger(__name__)

# ============================================================================
# Context Limits - Must match direct.py settings
# ============================================================================
MAX_NUM_RESULTS = 4   # Limit file_search results


def build_response_tools(toolgroup_selection, selected_vector_dbs, client):
    """Convert toolgroup selections to LlamaStack Responses API compatible tool format."""
    agent_tools = []

    for toolgroup_name in toolgroup_selection:
        if toolgroup_name == "builtin::rag":
            if len(selected_vector_dbs) > 0:
                vector_dbs = client.vector_stores.list() or []
                vector_db_ids = [
                    vector_db.id for vector_db in vector_dbs
                    if get_vector_db_name(vector_db) in selected_vector_dbs
                ]
                # FIXED: Add max_num_results to limit context size
                agent_tools.append({
                    "type": "file_search",
                    "vector_store_ids": list(vector_db_ids),
                    "max_num_results": MAX_NUM_RESULTS,
                })
        elif "web_search" in toolgroup_name or "search" in toolgroup_name.lower():
            agent_tools.append({"type": "web_search"})
        elif toolgroup_name.startswith("mcp::"):
            try:
                toolgroups = client.toolgroups.list()
                for toolgroup in toolgroups:
                    if str(toolgroup.identifier) == toolgroup_name:
                        agent_tools.append({
                            "type": "mcp",
                            "server_label": toolgroup.args.get("name", str(toolgroup.identifier)),
                            "server_url": toolgroup.mcp_endpoint.uri,
                        })
                        break
            except Exception as e:
                logger.debug("Failed to get MCP server info for %s: %s", toolgroup_name, e)
        else:
            try:
                tools_in_group = client.tools.list(toolgroup_id=toolgroup_name)
                for tool in tools_in_group:
                    agent_tools.append({
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description or "",
                            "parameters": tool.parameters or {}
                        }
                    })
            except Exception as e:
                logger.debug("Failed to get tools for %s: %s", toolgroup_name, e)

    return agent_tools


def handle_agent_file_search_chunk(state, selected_vector_dbs):
    """Handle file_search tool chunk in agent mode."""
    if state.tool_used:
        return
    if selected_vector_dbs:
        db_label = "vector store" if len(selected_vector_dbs) == 1 else "vector stores"
        status_msg = f"🛠 :grey[_Using file_search tool with {db_label}: {', '.join(selected_vector_dbs)}_]"
    else:
        status_msg = "🛠 :grey[_Using file_search tool..._]"
    state.tool_status = status_msg
    with state.containers.tool_status:
        st.markdown(status_msg)


def handle_agent_web_search_chunk(state):
    """Handle web_search tool chunk in agent mode."""
    if state.tool_used:
        return
    status_msg = "🛠 :grey[_Using web_search tool..._]"
    state.tool_status = status_msg
    with state.containers.tool_status:
        st.markdown(status_msg)


def handle_agent_output_item_done(chunk, state):
    """Handle response.output_item.done - tool execution completion with results."""
    if not hasattr(chunk, 'item'):
        return
    item = chunk.item
    item_type = getattr(item, 'type', None)

    if item_type == "file_search_call":
        if hasattr(item, 'results') and item.results:
            display_results = []
            for r in item.results:
                text = getattr(r, 'text', '')
                attrs = getattr(r, 'attributes', {})
                source = attrs.get('source') or getattr(r, 'filename', 'unknown')
                display_results.append({"source": source, "text": clean_text(text)})
            state.tool_results.append({
                'title': '📄 File Search Results',
                'type': 'json',
                'content': display_results
            })
            with state.containers.tool_results:
                with st.expander("📄 File Search Results", expanded=False):
                    st.json(display_results)
    elif item_type == "web_search_call":
        pass
    elif item_type == "function_call":
        if hasattr(item, 'output') and item.output:
            tool_name = getattr(item, 'name', 'function')
            state.tool_results.append({
                'title': f'🔧 Tool Output: {tool_name}',
                'type': 'code',
                'content': str(item.output)
            })
            with state.containers.tool_results:
                with st.expander(f"🔧 Tool Output: {tool_name}", expanded=False):
                    st.code(str(item.output))
    elif item_type == "mcp_call":
        if hasattr(item, 'output') and item.output:
            tool_name = getattr(item, 'name', 'mcp')
            state.tool_results.append({
                'title': f'🔧 MCP Tool Output: {tool_name}',
                'type': 'code',
                'content': str(item.output)
            })
            with state.containers.tool_results:
                with st.expander(f"🔧 MCP Tool Output: {tool_name}", expanded=False):
                    st.code(str(item.output))
    elif item_type and item_type.endswith("_call"):
        if hasattr(item, 'results') and item.results:
            formatted_name = item_type.replace("_", " ").title()
            state.tool_results.append({
                'title': f'🔧 {formatted_name} Results',
                'type': 'json',
                'content': item.results
            })
            with state.containers.tool_results:
                with st.expander(f"🔧 {formatted_name} Results", expanded=False):
                    st.json(item.results)
        elif hasattr(item, 'output') and item.output:
            formatted_name = item_type.replace("_", " ").title()
            state.tool_results.append({
                'title': f'🔧 {formatted_name} Output',
                'type': 'json',
                'content': item.output
            })
            with state.containers.tool_results:
                with st.expander(f"🔧 {formatted_name} Output", expanded=False):
                    st.json(item.output)


def handle_chunk_error(chunk):
    """Handle error chunk and return whether to stop streaming."""
    error_msg = "Unknown error"
    error_code = None
    if hasattr(chunk, 'error') and chunk.error:
        if hasattr(chunk.error, 'message'):
            error_msg = chunk.error.message
        if hasattr(chunk.error, 'code'):
            error_code = chunk.error.code
    elif hasattr(chunk, 'error_message'):
        error_msg = chunk.error_message
    error_display = f"❌ Error: {error_msg}"
    if error_code:
        error_display += f" (Code: {error_code})"
    st.error(error_display)
    logger.debug("Response failed: %s", error_msg)
    return True


def handle_chunk_completed(chunk):
    """Handle completed chunk."""
    logger.debug("Response completed successfully")
    if hasattr(chunk, 'stop_reason'):
        logger.debug("Stop reason: %s", chunk.stop_reason)


def handle_chunk_done(chunk, state):
    """Handle done chunk and finalize response."""
    has_output = (
        hasattr(chunk, 'response') and
        hasattr(chunk.response, 'output_text') and
        chunk.response.output_text
    )
    if has_output:
        state.full_response = chunk.response.output_text


def process_chunk_by_type(chunk, state, selected_vector_dbs):
    """Process a single chunk based on its type. Returns True to stop streaming."""
    chunk_type = chunk.type

    if chunk_type == "response.file_search_call.in_progress":
        handle_agent_file_search_chunk(state, selected_vector_dbs)
    elif chunk_type == "response.web_search_call.in_progress":
        handle_agent_web_search_chunk(state)
    elif chunk_type in ("response.web_search_call.searching", "response.web_search_call.completed"):
        pass
    elif chunk_type == "response.output_item.done":
        handle_agent_output_item_done(chunk, state)
    elif chunk_type == "response.reasoning_text.delta":
        if hasattr(chunk, 'delta') and chunk.delta:
            state.update_reasoning(chunk.delta)
    elif chunk_type == "response.output_text.delta":
        if hasattr(chunk, 'delta') and chunk.delta:
            state.update_message(chunk.delta)
    elif chunk_type == "response.failed":
        return handle_chunk_error(chunk)
    elif chunk_type == "response.completed":
        handle_chunk_completed(chunk)
    elif chunk_type == "response.done":
        handle_chunk_done(chunk, state)
    return False


def stream_agent_response(response, state, selected_vector_dbs):
    """Stream and process chunks from Responses API."""
    chunk_count = 0
    for chunk in response:
        chunk_count += 1
        logger.debug("Chunk #%s: type=%s", chunk_count, getattr(chunk, 'type', 'NO_TYPE'))
        logger.debug("  -> Full chunk: %s", chunk)
        if hasattr(chunk, 'type'):
            should_stop = process_chunk_by_type(chunk, state, selected_vector_dbs)
            if should_stop:
                break


def save_agent_response_to_session(state):
    """Save agent response to session state."""
    state.finalize_reasoning()
    state.finalize_message()
    response_dict = {
        "role": "assistant",
        "content": state.full_response,
        "stop_reason": "end_of_message"
    }
    if state.reasoning_text:
        response_dict["reasoning"] = state.reasoning_text
    if state.tool_status:
        response_dict["tool_status"] = state.tool_status
    if state.tool_results:
        response_dict["tool_results"] = state.tool_results
    st.session_state.messages.append(response_dict)


def agent_process_prompt(prompt, state, config):
    """Agent-based mode: Use Responses API with automatic tool calling."""
    tools = build_response_tools(
        config.toolgroup_selection, config.selected_vector_dbs, llama_stack_api.client
    ) if config.toolgroup_selection else None

    request_kwargs = {
        "model": config.model,
        "instructions": config.system_prompt,
        "input": prompt,
        "conversation": config.conversation_id,
        "temperature": config.sampling.temperature,
        "max_infer_iters": config.sampling.max_infer_iters,
        "stream": True,
    }

    if tools:
        request_kwargs["tools"] = tools

    logger.debug("Request: %s", request_kwargs)
    response = llama_stack_api.client.responses.create(**request_kwargs)

    stream_agent_response(response, state, config.selected_vector_dbs)
    save_agent_response_to_session(state)
