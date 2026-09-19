import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import asyncio

from llm.llm import generate_response
from llm.state_manager import StateManager

class InterruptionPipelineTest(unittest.IsolatedAsyncioTestCase):
    async def test_interruption_pipeline_rag(self):
        # 1. Setup a test agent schema with a mock summary in its global_prompt
        manager = StateManager("Updated_Real_Estate_Agent.json")
        manager.global_prompt = (
            "Complete Company Knowledge Summary:\n"
            "## Corporate Office\n"
            "Our headquarters is located in Gurgaon, Haryana.\n"
            "## Leadership\n"
            "The CEO of Suncity Projects is Mr. Ramesh Kumar.\n"
            "## Customer Support\n"
            "Contact our support desk at support@suncity.com or call 1800-123-456.\n"
            "## Company Overview\n"
            "We are Suncity Projects, a premier real estate builder.\n"
            "## Products and Services\n"
            "We build luxury apartments, residential villas, and commercial spaces.\n"
            "## Unique Value Proposition\n"
            "We deliver premium properties with RERA certification and direct bank loan approvals."
        )
        
        # Mock active node to ask for city
        manager.current_node_id = "node-1735267546732"
        manager.nodes = {"node-1735267546732": {"id": "node-1735267546732", "name": "Location & Budget"}}
        
        # Set language
        manager.conversation_data["active_language"] = "en"
        
        # Queries to verify
        test_cases = [
            ("Where is your headquarters?", "gurgaon"),
            ("Who is the CEO of the company?", "ramesh"),
            ("How do I contact customer support?", "support"),
            ("What does Suncity Projects do?", "real estate"),
            ("What products do you offer?", "luxury"),
            ("What makes Suncity unique?", "rera")
        ]
        
        # Mock classifier to always return company_question
        with patch("llm.llm._classify_message", new_callable=AsyncMock) as mock_classify:
            mock_classify.return_value = "company_question"
            
            # Mock Groq client completions to return answers based on query
            with patch("llm.llm._client.chat.completions.create", new_callable=AsyncMock) as mock_create:
                # We can configure a dynamic response mock based on query/messages
                def side_effect(model, messages, **kwargs):
                    user_msg = messages[-1]["content"]
                    ans = "I don't have that detail right now."
                    if "headquarters" in user_msg.lower():
                        ans = "Our headquarters is located in Gurgaon, Haryana."
                    elif "ceo" in user_msg.lower():
                        ans = "The CEO of Suncity Projects is Mr. Ramesh Kumar."
                    elif "support" in user_msg.lower():
                        ans = "Contact our support desk at support@suncity.com or call 1800-123-456."
                    elif "unique" in user_msg.lower():
                        ans = "We deliver premium properties with RERA certification."
                    elif "products" in user_msg.lower() or "offer" in user_msg.lower():
                        ans = "We build luxury apartments, residential villas, and commercial spaces."
                    elif "do?" in user_msg.lower() or "what does" in user_msg.lower() or "suncity" in user_msg.lower():
                        ans = "We are Suncity Projects, a premier real estate builder."
                    
                    mock_resp = MagicMock()
                    mock_resp.choices = [
                        MagicMock(message=MagicMock(content=ans))
                    ]
                    return mock_resp
                    
                mock_create.side_effect = side_effect

                for query, expected_substring in test_cases:
                    with patch("llm.llm.logger.info") as mock_log:
                        response, is_terminal = await generate_response(
                            user_text=query,
                            state_manager=manager,
                            allow_transition=False
                        )
                        
                        # Check response properties
                        self.assertIsNotNone(response)
                        self.assertIn(expected_substring, response.lower())
                        self.assertFalse(is_terminal)
                        
                        # Inspect logs to verify the correct answer source and semantic retrieval
                        log_calls = [call.args[0] for call in mock_log.call_args_list if call.args]
                        log_output = "\n".join(log_calls)
                        
                        self.assertIn("Semantic Retrieval Started: True", log_output)
                        self.assertTrue("Final Answer Source: Summary" in log_output or "Final Answer Source: LLM" in log_output)

if __name__ == "__main__":
    unittest.main()
