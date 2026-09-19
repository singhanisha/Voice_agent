"""
Main Pipeline Logic.

This script wires up the Pipecat framework and connects the modules:
  STT -> LLM -> TTS
  
Usage: python main_pipeline.py
"""

import asyncio
import logging
from dotenv import load_dotenv

try:
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineTask
    from pipecat.frames.frames import EndFrame
except ImportError:
    logging.fatal("pipecat-ai not found. Please install it.")
    exit(1)

from flows.runtime import RealEstateSTTProcessor, RealEstateLLMProcessor, RealEstateTTSProcessor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def run_agent():
    load_dotenv()
    
    # 1. Initialize Custom Pipecat Processors
    stt_processor = RealEstateSTTProcessor()
    llm_processor = RealEstateLLMProcessor()
    tts_processor = RealEstateTTSProcessor()
    
    # In a full telephony implementation (Task 7 VoBiz), we would attach a Transport here.
    # For now, we mock the pipeline structure exactly as Pipecat expects it.
    logger.info("Initializing Pipecat Voice Agent Pipeline...")
    
    pipeline = Pipeline([
        stt_processor,
        llm_processor,
        tts_processor
    ])

    # 2. Setup Runner and Task
    runner = PipelineRunner()
    task = PipelineTask(pipeline)

    logger.info("Pipeline Ready. Awaiting Transport connection in future tasks...")
    # await runner.run(task) # Disabled until we have a Transport layer (like Task 7)

if __name__ == "__main__":
    try:
        asyncio.run(run_agent())
    except KeyboardInterrupt:
        logger.info("Agent shut down manually.")
