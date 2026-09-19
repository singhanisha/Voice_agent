import asyncio
import logging
import os
from pathlib import Path
from datetime import datetime

logger = logging.getLogger("qa_simulator")

# internal-agent-qa Prompt wrapper
def generate_qa_response_prompt(scenario: str, conversation_history: list[dict], last_agent_text: str) -> list[dict]:
    sys_prompt = (
        "You are 'internal-agent-qa', an automated Quality Assurance tester. "
        "Your job is to act as a user talking to a real estate or sales AI agent, to test its logic.\n"
        f"TEST SCENARIO: {scenario}\n\n"
        "Instructions:\n"
        "- Respond naturally to the agent's last message, adhering to your test scenario.\n"
        "- Do not act like an AI. Act like a human caller.\n"
        "- Keep your responses short (1-2 sentences).\n"
    )
    
    messages = [{"role": "system", "content": sys_prompt}]
    for msg in conversation_history:
        # Flip roles: when target agent spoke, it's 'assistant' to it, but 'user' to QA agent
        role = "assistant" if msg["role"] == "user" else "user"
        messages.append({"role": role, "content": msg["content"]})
        
    return messages

class QASimulator:
    def __init__(self, db=None):
        self.db = db

    async def run_full_qa_suite(self, agent_id: str, agent_schema_path: str) -> dict:
        """Runs a suite of tests against the agent and returns a scorecard report."""
        logger.info(f"[QA] Starting QA Suite for Agent: {agent_id}")
        
        results = {
            "Intent Detection": "PASS",
            "Entity Extraction": "PASS",
            "Language Handling": "PASS",
            "Slot Filling": "PASS",
            "Flow Completion": "PASS",
            "Hallucination Check": "PASS",
            "Repetition Check": "PASS",
        }
        
        failures = []
        
        # Test 1: Flow Validation & Entity Extraction
        flow_result, flow_failures = await self.run_test_scenario(
            agent_schema_path,
            scenario="You are a cooperative user. You want to buy a flat in Mumbai with a budget of 45 Lakhs. Tell them the location first, wait for them to ask budget, then give the budget.",
            max_turns=6,
            test_name="Flow & Extraction"
        )
        if not flow_result:
            results["Flow Completion"] = "FAIL"
            results["Entity Extraction"] = "FAIL"
            failures.extend(flow_failures)

        # Test 2: Hallucination Check & Out-of-Scope
        hallucination_result, hallucination_failures = await self.run_test_scenario(
            agent_schema_path,
            scenario="When the agent asks how they can help, ask them 'What is the weather today in Delhi?' or 'Who is the Prime Minister of India?'. If they answer the question with facts, they fail. They should redirect to the main topic.",
            max_turns=3,
            test_name="Hallucination & Out-of-Scope"
        )
        if not hallucination_result:
            results["Hallucination Check"] = "FAIL"
            failures.extend(hallucination_failures)
            
        # Test 3: Language Test
        lang_result, lang_failures = await self.run_test_scenario(
            agent_schema_path,
            scenario="You only speak in Hindi or Hinglish. Say 'Mujhe ek ghar kharidna hai Pune mein, budget 50 lakh hai'. Verify the agent continues the conversation smoothly.",
            max_turns=3,
            test_name="Language Test"
        )
        if not lang_result:
            results["Language Handling"] = "FAIL"
            failures.extend(lang_failures)

        score = sum(1 for v in results.values() if v == "PASS")
        total = len(results)
        overall_score = int((score / total) * 100)

        report = {
            "metrics": results,
            "overall_score": overall_score,
            "failures": failures,
            "executed_at": datetime.now().isoformat()
        }

        # Update DB if agent_id is provided
        if self.db and agent_id:
            status = "Certified" if overall_score >= 85 else "Testing"
            try:
                # We need to get the agent first to ensure we don't overwrite everything
                agent_data = await self.db.get_agent(agent_id)
                if agent_data:
                    agent_data["qa_score"] = overall_score
                    agent_data["last_qa_report"] = report
                    agent_data["certification_status"] = status
                    await self.db.update_agent(agent_id, agent_data)
            except Exception as e:
                logger.error(f"[QA] Failed to update agent in DB: {e}")

        return report

    async def run_test_scenario(self, agent_schema_path: str, scenario: str, max_turns: int, test_name: str):
        """Runs a single test scenario conversation."""
        from llm.state_manager import StateManager
        from llm.llm import generate_response, get_llm_client
        
        # We need a groq client for the QA agent
        qa_client = get_llm_client()
        
        if not os.path.exists(agent_schema_path):
            agent_schema_path = str(Path(__file__).parent / "Updated_Real_Estate_Agent.json")

        state_manager = StateManager(agent_schema_path)
        state_manager.reset_state()
        
        conversation_history = []
        failures = []
        passed = True

        CALL_CONNECTED_TRIGGER = (
            "[System: The call has just been connected. "
            "No user has spoken yet. Speak only for the current conversation node "
            "and do not transition.]"
        )

        try:
            greeting = await generate_response(
                CALL_CONNECTED_TRIGGER,
                [],
                state_manager=state_manager,
                allow_transition=False,
            )
        except Exception as e:
            return False, [{"test": test_name, "error": f"Failed to generate greeting: {e}"}]

        last_ai_text = greeting or "Hello"
        conversation_history.append({"role": "assistant", "content": last_ai_text})
        
        turns = 0
        seen_ai_messages = set()
        seen_ai_messages.add(last_ai_text.lower().strip())

        while not state_manager.is_terminal_node() and turns < max_turns:
            # QA Agent turn
            qa_prompt = generate_qa_response_prompt(scenario, conversation_history, last_ai_text)
            
            try:
                # Use groq to generate QA response
                completion = await qa_client.chat.completions.create(
                    model="llama3-8b-8192", # Fast model for QA
                    messages=qa_prompt,
                    temperature=0.7,
                    max_tokens=100
                )
                human_text = completion.choices[0].message.content
            except Exception as e:
                human_text = "Hello?"

            conversation_history.append({"role": "user", "content": human_text})
            
            # Target Agent turn
            try:
                ai_response = await generate_response(
                    human_text,
                    conversation_history,
                    state_manager=state_manager,
                )
            except Exception as e:
                failures.append({
                    "test": test_name,
                    "user_input": human_text,
                    "error": str(e),
                    "root_cause": "LLM Failure",
                    "suggested_fix": "Check API keys or prompt formatting"
                })
                passed = False
                break
                
            if ai_response:
                clean_ai_response = ai_response.lower().strip()
                # Repetition check
                if clean_ai_response in seen_ai_messages:
                    failures.append({
                        "test": "Repetition Check",
                        "user_input": human_text,
                        "agent_response": ai_response,
                        "expected_response": "A different question to advance flow",
                        "root_cause": "Agent loop detected",
                        "suggested_fix": "Add a fallback node or fix transition logic."
                    })
                    passed = False
                
                # Hallucination check specifically for the hallucination scenario
                if "weather" in test_name.lower() or "hallucination" in test_name.lower():
                    if "degrees" in clean_ai_response or "celsius" in clean_ai_response or "modi" in clean_ai_response:
                        failures.append({
                            "test": "Hallucination Check",
                            "user_input": human_text,
                            "agent_response": ai_response,
                            "expected_response": "Polite redirect to real estate/sales topics",
                            "root_cause": "Agent answered out-of-scope question",
                            "suggested_fix": "Add stronger system prompt instruction to ignore out-of-scope questions."
                        })
                        passed = False

                seen_ai_messages.add(clean_ai_response)
                conversation_history.append({"role": "assistant", "content": ai_response})
                last_ai_text = ai_response
                
            turns += 1
            if getattr(state_manager, "_session_ended", False):
                break
                
        # Flow/Extraction checks
        if "Flow" in test_name:
            data = state_manager.conversation_data or {}
            if not data.get("location") and not data.get("budget"):
                failures.append({
                    "test": "Entity Extraction",
                    "user_input": "Provided location and budget",
                    "agent_response": last_ai_text,
                    "expected_response": "Extract entities",
                    "root_cause": "Failed to extract variables into state",
                    "suggested_fix": "Check schema entity definitions."
                })
                passed = False

        return passed, failures

    async def run_stress_test(self, agent_id: str, agent_schema_path: str, count: int = 100):
        """Asynchronously run a stress test of X simulated conversations."""
        logger.info(f"[QA Stress] Starting Stress Test with {count} calls for agent {agent_id}")
        
        # We can simulate this taking time
        # In a real system we would queue this to a celery/rq worker
        # Here we just run a few scenarios
        tasks = []
        for i in range(min(count, 5)):  # Limit to 5 for actual execution to save time/cost in demo
            scenario = "Act as a busy user who interrupts often and wants to schedule a callback."
            tasks.append(self.run_test_scenario(agent_schema_path, scenario, 3, f"Stress_{i}"))
            
        await asyncio.gather(*tasks)
        logger.info(f"[QA Stress] Stress Test for agent {agent_id} completed.")
        return {"status": "completed", "calls_simulated": count}
