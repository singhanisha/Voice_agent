import unittest
from llm.llm import (
    _extract_summary_from_prompt,
    _retrieve_semantic_chunks_tfidf,
    _get_resume_bridge,
)

class InterruptionLayerTest(unittest.TestCase):
    def test_extract_summary_from_prompt(self):
        prompt = (
            "Some instructions...\n\n"
            "Complete Company Knowledge Summary:\n"
            "## Overview\n"
            "This is a test summary about Acme Corp.\n"
            "## Contact\n"
            "Call us at 555-0199."
        )
        summary = _extract_summary_from_prompt(prompt)
        self.assertIn("## Overview", summary)
        self.assertIn("Call us at 555-0199.", summary)

    def test_retrieve_semantic_chunks_synonyms(self):
        summary = (
            "## Overview\n"
            "We are a software developer office.\n"
            "## Contact Information\n"
            "Our email support is support@acme.com and address is Pune, India.\n"
            "## Pricing Plans\n"
            "Basic cost is $10 per month."
        )
        
        # Test contact matching support synonym
        headings, chunks, scores = _retrieve_semantic_chunks_tfidf("How do I contact helpdesk?", summary)
        self.assertIn("Contact Information", headings)
        
        # Test pricing matching cost synonym
        headings, chunks, scores = _retrieve_semantic_chunks_tfidf("What is the cost?", summary)
        self.assertIn("Pricing Plans", headings)

    def test_get_resume_bridge(self):
        current_node = {
            "name": "ask_city",
            "collects": ["city"],
            "response": "Which city are you interested in?"
        }
        context = {}
        
        # English
        resume_q, bridge = _get_resume_bridge(current_node, context, "en")
        self.assertEqual(resume_q, "Which city are you interested in")
        self.assertEqual(bridge, "Now, coming back to our discussion, Which city are you interested in?")
        
        # Hindi
        resume_q, bridge = _get_resume_bridge(current_node, context, "hi")
        self.assertEqual(bridge, "Toh, hamari baat-cheet par waapas aate hain, Which city are you interested in?")

if __name__ == "__main__":
    unittest.main()
