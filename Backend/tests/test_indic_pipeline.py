import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from intelligence.pipeline import ConversationState, workflow
from core_voice_loop import CallSession
from langchain_core.messages import HumanMessage

@pytest.mark.asyncio
async def test_latency_masking_filler_yield():
    # Setup state
    state: ConversationState = {
        "messages": [HumanMessage(content="Is Suncity available?")],
        "extracted_slots": {
            "intent": "purchase",
            "budget": "high",
            "bhk": "3",
            "timeline": None
        },
        "current_node": "LIVE_SEARCH",
        "pending_filler_action": None,
        "rag_context": None,
        "retry_count": 0,
        "user_input": "Is Suncity available?"
    }
    
    # Mock crawler
    mock_crawler = AsyncMock()
    mock_crawler.fetch_and_parse.return_value = "Suncity is available for 3 BHK."
    
    engine = workflow.compile()
    
    # Run first turn (expect filler)
    with patch("intelligence.pipeline.crawler_instance", mock_crawler):
        state = await engine.ainvoke(state)
        
        assert state["pending_filler_action"] is not None
        assert "pull up the live board" in state["pending_filler_action"]
        assert state["rag_context"] == "PENDING"
        
        # Reset filler like core_voice_loop does
        state["pending_filler_action"] = None
        
        # Run second turn (expect crawler result)
        state = await engine.ainvoke(state)
        
        assert state["rag_context"] == "Suncity is available for 3 BHK."
        assert mock_crawler.fetch_and_parse.called

@pytest.mark.asyncio
async def test_vad_and_stt_pipeline():
    session = CallSession(system_prompt="Test")
    session.stt_engine = MagicMock()
    session.stt_engine.async_transcribe = AsyncMock(return_value="Yes, I want a 3 BHK")
    
    session.vad_buffer = MagicMock()
    session.vad_buffer.process_chunk = MagicMock(return_value=b"fake_payload")
    session.vad_buffer._calculate_rms = MagicMock(return_value=100)
    session.vad_buffer.energy_threshold = 500
    
    session.on_transcript = AsyncMock()
    session._running = True
    
    # Push chunk
    await session.audio_in_queue.put(b"chunk1")
    
    # Start just the listen task
    listen_task = asyncio.create_task(session._listen_task())
    
    # Wait a bit for processing
    await asyncio.sleep(0.1)
    
    # Verify process_chunk was called
    session.vad_buffer.process_chunk.assert_called_with(b"chunk1")
    # Verify STT was called with payload
    session.stt_engine.async_transcribe.assert_called_with(b"fake_payload")
    
    # Verify transcript is queued
    transcript = await session.transcript_queue.get()
    assert transcript == "Yes, I want a 3 BHK"
    
    # Cleanup
    session._running = False
    listen_task.cancel()
