'use client';
import { useState, useEffect } from 'react';
import { Bot, FileText } from 'lucide-react';
import DashboardLayout from '@/components/DashboardLayout';
import FlowPreviewModal from '@/components/FlowPreviewModal';
import QATestModal from '@/components/QATestModal';
import GenerateSummaryModal from '@/components/GenerateSummaryModal';
import { useAuth } from '@/context/AuthContext';
import { getProviderLabel } from '@/lib/providerDisplay';

const AGENT_TYPE_TEMPLATES = {
  real_estate_sales: {
    label: 'Real Estate Sales',
    fields: 'interested, budget, location, property_type, timeline, callback',
    prompt: `You are Priya, a senior property consultant at a trusted real estate advisory firm. You are speaking with a potential buyer or investor who has just enquired about a property.

## Who You Are
You are NOT a bot. You are a warm, experienced human consultant who genuinely cares about helping people find the right home or investment. You have deep knowledge of Indian real estate markets — Pune, Mumbai, Bangalore, Hyderabad, Delhi NCR, and other major cities.

## How You Speak
- Sound exactly like a friendly, knowledgeable human — not a scripted assistant.
- Keep every response short and natural, the way a person speaks in a phone conversation.
- Make a brief statement or observation BEFORE asking your next question. Never fire questions one after another.
- Use natural fillers where appropriate: "Sure," "Got it," "Absolutely," "That makes sense," "Of course," "No worries at all."
- Mirror the customer's language automatically — if they speak in Hindi, respond in Hindi. If they use Hinglish, match it naturally.
- If the customer volunteers information, acknowledge it warmly and don't ask about it again.
- Pause and react to what they say before moving forward. Make them feel heard.

## Your Goal
Have a natural conversation that gradually uncovers:
1. Whether they want to buy, rent, or invest
2. Which city, area, or project interests them
3. Their approximate budget
4. The type of property (1 BHK, 2 BHK, 3 BHK, villa, plot, commercial)
5. Their timeline (immediate / this month / 3 months / just exploring)
6. The best follow-up (callback from specialist / WhatsApp options / brochure / site visit)

Only gather what is still unknown. Never re-ask what the customer has already told you.

## Conversation Opening
Start warm and open. Example:
"Hi! Thanks for reaching out to us. I'm Priya from the advisory team. Happy to help you today. Could you tell me a bit about what you're looking for?"

## Natural Discovery — Make Statements, Then Ask
Never jump straight into questions. Acknowledge first, then move the conversation forward naturally.

BAD (robotic): "What is your budget?"
GOOD (human): "That area has some really lovely options right now. Just to help me suggest the right ones — are you working with a particular budget in mind?"

BAD: "What property type do you need?"
GOOD: "A lot of families looking in that area prefer 2 or 3 BHKs. Is that the kind of space you had in mind, or were you thinking of something different?"

BAD: "When do you plan to buy?"
GOOD: "It sounds like you've been thinking about this for a while. Is this something you're looking to move on fairly soon, or are you still in the early research stage?"

## Handling Sensitive Questions
If they ask about exact pricing, possession date, availability, offers, RERA, loans, or legal clearances — NEVER guess or promise. Say warmly:
"That's a great question — I'd rather have our specialist confirm the latest details for you so you have the most accurate picture."

## Handling Objections Naturally
If they say they're just exploring:
"No pressure at all — actually, this is the best time to look around and understand the market before making any decisions. I'll keep it light — just want to get a sense of what would work for you."

If they're unsure about budget:
"Completely fine — even a rough range like 'somewhere between X and Y' is enough. We work across all segments so there's no pressure."

If they seem busy or in a hurry:
"Of course — I'll make this quick. Or if it's easier, I can have someone reach out at a time that's convenient for you. What works better?"

## Closing the Conversation
Once you have enough information, summarize warmly and naturally in one or two sentences — the way a consultant would wrap up a call.

Example:
"Perfect, so you're looking at a 2 BHK in Whitefield, budget around ₹80 to 90 lakh, and you'd like to buy within the next couple of months — that's really helpful."

Then transition to the next step:
"What would be most helpful right now — should I have one of our property specialists give you a quick call, or would you prefer I send across some matching options on WhatsApp first?"

Always close warmly:
"Thank you so much for your time today. We'll make sure you hear from the right person very soon. Take care and have a great day!"

## Core Rules — Never Break These
- One topic at a time. One question at a time. Always.
- React to what the customer just said before moving forward.
- Never sound like a form or a checklist.
- Never fabricate prices, availability, or project details.
- Never repeat a question the customer has already answered.
- The customer should feel like they just had a great conversation with a real human consultant — not an automated system.`
  },
  finance: {
    label: 'Finance Advisory',
    fields: 'interested, product_interest, income_range, loan_amount, timeline, callback',
    prompt: `You are a compliant finance advisory voice agent.

Primary goal:
Understand the customer's interest in financial products such as personal loans, business loans, credit cards, insurance, or investments and qualify them for a human advisor.

Conversation style:
Be calm, trustworthy, and precise. Ask one question at a time. Avoid pressure tactics. Keep the conversation short and respectful.

Discovery questions:
1. Ask which financial product they are interested in.
2. Ask the purpose, required amount, or preferred plan.
3. Ask broad eligibility details only when appropriate, such as income range or employment type.
4. Ask their timeline for taking a decision.
5. Ask for permission to arrange a callback from an advisor.

Compliance rules:
Do not guarantee approval, returns, interest rates, tax benefits, or eligibility. Do not collect sensitive data such as OTPs, full card numbers, passwords, bank PINs, Aadhaar numbers, or account login details.

Final outcome:
Summarize the customer's need and confirm that an authorized advisor will follow up.`
  },
  insurance: {
    label: 'Insurance Renewal',
    fields: 'interested, policy_type, renewal_date, coverage_need, family_members, callback',
    prompt: `You are an insurance renewal and advisory voice agent.

Primary goal:
Help the customer review or renew insurance coverage and identify whether they need a callback from an advisor.

Conversation style:
Be empathetic, clear, and low-pressure. Use plain language. Ask one question at a time.

Discovery questions:
1. Ask whether they are interested in health, life, motor, or business insurance.
2. Ask if this is a renewal, new policy, or comparison request.
3. Ask renewal date or urgency.
4. Ask basic coverage preference such as individual, family, or vehicle.
5. Ask whether they want plan options shared on WhatsApp/email or a callback.

Compliance rules:
Do not guarantee claim approval, premium, coverage, or policy issuance. Do not collect sensitive documents or payment details over the call.

Final outcome:
Confirm the requested insurance type, urgency, and preferred follow-up mode.`
  },
  education: {
    label: 'Education Counselling',
    fields: 'interested, course_interest, education_level, city, budget, callback',
    prompt: `You are an education counselling voice agent.

Primary goal:
Understand the student's course interest and connect them with the right counsellor.

Conversation style:
Be encouraging, patient, and clear. Ask one question at a time. Support parents and students without sounding pushy.

Discovery questions:
1. Ask which course, program, exam, or career path they are interested in.
2. Ask current education level.
3. Ask preferred city, online/offline preference, and timeline.
4. Ask budget or fee range only if the conversation naturally allows it.
5. Ask whether a counsellor should call back.

Rules:
Do not guarantee admission, scholarship, visa approval, placement, or exam results.

Final outcome:
Summarize the student requirement and confirm the counselling follow-up.`
  }
};

const DEFAULT_AGENT_TYPE = 'real_estate_sales';
const FLOW_VISUALIZATION_ENABLED = process.env.NEXT_PUBLIC_FLOW_VISUALIZATION_ENABLED === 'true';
const SCRAPE_GENERATE_SCRIPT_ENABLED = process.env.NEXT_PUBLIC_SCRAPE_GENERATE_SCRIPT_ENABLED === 'true';
const SCRAPE_WORKER_V1_ENABLED = process.env.NEXT_PUBLIC_SCRAPE_WORKER_V1_ENABLED === 'true';
const SCRAPE_POLL_INTERVAL_MS = 1500;
const SCRAPE_POLL_ATTEMPTS = 30;
const SCRAPE_REUSE_FINAL_STATUSES = ['completed', 'draft_ready'];
const CARTESIA_FEMALE_VOICES = [
  {
    label: 'Hinglish Speaking Lady - Indian multilingual (recommended)',
    value: '95d51f79-c397-46f9-b49a-23763d3eaa2d'
  },
  {
    label: 'Indian Customer Support Lady - phone support',
    value: 'ff1bb1a9-c582-4570-9670-5f46169d0fc8'
  },
  {
    label: 'Indian Lady - Indian accent fallback',
    value: '3b554273-4299-48b9-9aaf-eefd438e3941'
  },
  {
    label: 'Hindi Narrator Woman - Hindi-focused',
    value: 'c1abd502-9231-4558-a054-10ac950c356d'
  },
  {
    label: 'Katie - US English voice agent fallback',
    value: 'f786b574-daa5-4673-aa0c-cbe3e8534c02'
  },
  {
    label: 'Tessa - US English expressive fallback',
    value: '6ccbfb76-1fc6-48f7-b71d-91ac6298247b'
  }
];
const DEFAULT_CARTESIA_VOICE_ID = CARTESIA_FEMALE_VOICES[0].value;

const SMALLEST_FEMALE_VOICES = [
  { label: 'Anika - Sales / Conversational (Recommended)', value: 'anika' },
  { label: 'Divya - Customer Support / Conversational', value: 'divya' },
  { label: 'Avni - Customer Support / Conversational', value: 'avni' },
  { label: 'Kavya - Insurance Agent', value: 'kavya' },
  { label: 'Naina - Conversational / Narration', value: 'naina' },
  { label: 'Nikita - Conversational / Narration', value: 'nikita' },
  { label: 'Siya - Customer Support / Conversational', value: 'siya' },
  { label: 'Aditi - Narration / Educational', value: 'aditi' },
  { label: 'Maya - Conversational / Advertisement', value: 'maya' },
  { label: 'Rishika - Insurance Agent / Narration', value: 'rishika' },
  { label: 'Maithili - Customer Support / Narration', value: 'maithili' },
  { label: 'Aisha - Sales / Advertisement', value: 'aisha' },
];
const SMALLEST_MALE_VOICES = [
  { label: 'Devansh - Sales / Advertisement (Recommended)', value: 'devansh' },
  { label: 'Dhruv - Sales / Narration', value: 'dhruv' },
  { label: 'Karan - Insurance Agent / Narration', value: 'karan' },
  { label: 'Arjun - Insurance Agent / Educational', value: 'arjun' },
  { label: 'Vivaan - Sales / Narration', value: 'vivaan' },
  { label: 'Atharv - Sales / Conversational', value: 'atharv' },
  { label: 'Veer - Customer Support / Conversational', value: 'veer' },
  { label: 'Kunal - Narration / Educational', value: 'kunal' },
  { label: 'Dhruv - Advertisement / Narration', value: 'dhruv' },
  { label: 'Wasim - Sales / Advertisement', value: 'wasim' },
  { label: 'Siddharth - Narration / Educational', value: 'siddharth' },
  { label: 'Kaustubh - Insurance Agent / Narration', value: 'kaustubh' },
];
const DEFAULT_SMALLEST_VOICE = 'anika';

// =========================================================
// NVIDIA MAGPIE MULTILINGUAL VOICES
// =========================================================

const MAGPIE_FEMALE_VOICES = [
  { value: 'Sofia', label: 'Sofia — Warm & Expressive (Native Hindi & English)', },
  { value: 'Aria', label: 'Aria — Professional & Articulate (Native English & Hindi)',},
  { value: 'Mia', label: 'Mia — Dynamic & Friendly', },
];

const MAGPIE_MALE_VOICES = [
  { value: 'Jason', label: 'Jason — Deep & Professional (Native English & Hindi)', },
  { value: 'Leo', label: 'Leo — Friendly & Conversational (Native Hindi & English)', },
  { value: 'Ray',label: 'Ray — Energetic & Modern', },
  { value: 'Pascal', label: 'Pascal — Deep & Professional',},
];

const DEFAULT_MAGPIE_VOICE = 'Sofia';

// Sarvam voice names — selecting these auto-sets tts_provider to 'sarvam'
const SARVAM_VOICE_NAMES = new Set(['shreya', 'ishita', 'shubh', 'priya', 'neha', 'aditya', 'ashutosh']);

/* const makeInitialFormData = (overrides = {}) => ({
  name: '',
  voice: 'shreya',
  language: 'English',
  max_duration: 300,
  provider: 'twilio',
  stt_provider: 'groq',
  tts_provider: 'sarvam',
  cartesia_voice_id: DEFAULT_CARTESIA_VOICE_ID,
  smallest_voice: DEFAULT_SMALLEST_VOICE,
  assigned_email: '',
  agent_type: DEFAULT_AGENT_TYPE,
  script: AGENT_TYPE_TEMPLATES[DEFAULT_AGENT_TYPE].prompt,
  data_fields: AGENT_TYPE_TEMPLATES[DEFAULT_AGENT_TYPE].fields,
  custom_json: null,
  ...overrides
});
 */

const makeInitialFormData = (overrides = {}) => ({
  name: '',
  voice: 'shreya',
  language: 'English',
  max_duration: 300,
  provider: 'twilio',
  stt_provider: 'groq',
  tts_provider: 'sarvam',
  cartesia_voice_id: DEFAULT_CARTESIA_VOICE_ID,
  smallest_voice: DEFAULT_SMALLEST_VOICE,
  magpie_speaker: DEFAULT_MAGPIE_VOICE, // <--- ADD THIS
  assigned_email: '',
  agent_type: DEFAULT_AGENT_TYPE,
  script: AGENT_TYPE_TEMPLATES[DEFAULT_AGENT_TYPE].prompt,
  data_fields: AGENT_TYPE_TEMPLATES[DEFAULT_AGENT_TYPE].fields,
  custom_json: null,
  ...overrides
});


/* const formDataFromAgent = (agent) => {
  const agentType = agent.agent_type || DEFAULT_AGENT_TYPE;
  const template = AGENT_TYPE_TEMPLATES[agentType] || AGENT_TYPE_TEMPLATES[DEFAULT_AGENT_TYPE];
  return makeInitialFormData({
    name: agent.name || '',
    voice: agent.voice || '11labs-06nek6zjTCD1vCbtc8bc',
    language: agent.language || 'English',
    max_duration: agent.max_duration || 300,
    provider: agent.provider || 'twilio',
    stt_provider: agent.stt_provider || 'groq',
    tts_provider: agent.tts_provider || 'edge',
    cartesia_voice_id: agent.cartesia_voice_id || DEFAULT_CARTESIA_VOICE_ID,
    smallest_voice: agent.smallest_voice || DEFAULT_SMALLEST_VOICE,
    assigned_email: agent.assigned_email || '',
    agent_type: agentType,
    script: agent.script || template.prompt,
    data_fields: Array.isArray(agent.data_fields) ? agent.data_fields.join(', ') : agent.data_fields || template.fields,
    custom_json: null
  });
}; */

const formDataFromAgent = (agent) => {
  const agentType = agent.agent_type || DEFAULT_AGENT_TYPE;
  const template = AGENT_TYPE_TEMPLATES[agentType] || AGENT_TYPE_TEMPLATES[DEFAULT_AGENT_TYPE];

  // Extract speaker name if stored as Magpie-Multilingual.HI-IN.Sofia
  let initialMagpieSpeaker = DEFAULT_MAGPIE_VOICE;
  if (agent.tts_provider === 'magpie' && agent.voice) {
    const parts = agent.voice.split('.');
    initialMagpieSpeaker = parts.length >= 3 ? parts[2] : agent.voice;
  }

  return makeInitialFormData({
    name: agent.name || '',
    voice: agent.voice || '11labs-06nek6zjTCD1vCbtc8bc',
    language: agent.language || 'English',
    max_duration: agent.max_duration || 300,
    provider: agent.provider || 'twilio',
    stt_provider: agent.stt_provider || 'groq',
    tts_provider: agent.tts_provider || 'edge',
    cartesia_voice_id: agent.cartesia_voice_id || DEFAULT_CARTESIA_VOICE_ID,
    smallest_voice: agent.smallest_voice || DEFAULT_SMALLEST_VOICE,
    magpie_speaker: initialMagpieSpeaker, // <--- ADD THIS
    assigned_email: agent.assigned_email || '',
    agent_type: agentType,
    script: agent.script || template.prompt,
    data_fields: Array.isArray(agent.data_fields) ? agent.data_fields.join(', ') : agent.data_fields || template.fields,
    custom_json: null
  });
};


const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function qualityClass(level) {
  if (level === 'high') return 'bg-success-subtle text-success border-success-subtle';
  if (level === 'medium') return 'bg-primary-subtle text-primary border-primary-subtle';
  if (level === 'low') return 'bg-warning-subtle text-warning border-warning-subtle';
  return 'bg-secondary-subtle text-secondary border-secondary-subtle';
}

function draftQuality(draft) {
  return draft?.knowledge?.quality || null;
}

function sourceEvidenceFromKnowledge(knowledge) {
  const values = [];
  const add = (items) => {
    (items || []).forEach((item) => {
      if (typeof item === 'string') values.push(item);
    });
  };
  if (knowledge?.source_url) values.push(knowledge.source_url);
  add(knowledge?.company?.evidence);
  (knowledge?.products_or_services || []).forEach((item) => add(item.evidence));
  (knowledge?.value_propositions || []).forEach((item) => add(item.evidence));
  (knowledge?.faqs || []).forEach((item) => add(item.evidence));
  (knowledge?.pages_crawled || []).forEach((page) => {
    if (page?.url) values.push(page.url);
  });
  return [...new Set(values.filter(Boolean))].slice(0, 6);
}

function contentInventoryItems(knowledge) {
  const pageTypes = knowledge?.content_inventory?.page_types || {};
  return Object.entries(pageTypes)
    .filter(([, count]) => Number(count) > 0)
    .sort(([left], [right]) => left.localeCompare(right))
    .slice(0, 8);
}

function guidanceSummary(knowledge) {
  const questions = (knowledge?.qualification_questions || []).filter(Boolean).slice(0, 4);
  const objections = (knowledge?.objections || [])
    .filter((item) => item?.intent || item?.guidance)
    .slice(0, 4);
  const faqs = (knowledge?.faqs || [])
    .filter((item) => item?.question)
    .slice(0, 4);
  return {
    questions,
    objections,
    faqs,
    hasItems: Boolean(questions.length || objections.length || faqs.length),
  };
}

function ConversationGuidance({ knowledge }) {
  const guidance = guidanceSummary(knowledge);
  if (!guidance.hasItems) return null;

  return (
    <div className="border-top mt-3 pt-3 small">
      <div className="text-muted mb-1">Conversation Guidance</div>
      <div className="row g-2">
        {guidance.questions.length > 0 && (
          <div className="col-md-4">
            <div className="fw-semibold mb-1">Qualification</div>
            <ul className="mb-0 ps-3">
              {guidance.questions.map((question, index) => (
                <li key={`${question}-${index}`}>{question}</li>
              ))}
            </ul>
          </div>
        )}
        {guidance.objections.length > 0 && (
          <div className="col-md-4">
            <div className="fw-semibold mb-1">Objections</div>
            <ul className="mb-0 ps-3">
              {guidance.objections.map((item, index) => (
                <li key={`${item.intent || 'objection'}-${index}`}>
                  <span className="text-capitalize">{String(item.intent || 'objection').replaceAll('_', ' ')}</span>
                  {item.guidance ? `: ${item.guidance}` : ''}
                </li>
              ))}
            </ul>
          </div>
        )}
        {guidance.faqs.length > 0 && (
          <div className="col-md-4">
            <div className="fw-semibold mb-1">FAQs</div>
            <ul className="mb-0 ps-3">
              {guidance.faqs.map((item, index) => (
                <li key={`${item.question}-${index}`}>{item.question}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

export default function AgentsPage() {
  const { user, activeClient } = useAuth();
  const [agents, setAgents] = useState([]);
  const [showModal, setShowModal] = useState(false);
  const [modalMode, setModalMode] = useState('create');
  const [editingAgentId, setEditingAgentId] = useState(null);
  const [loading, setLoading] = useState(true);
  const [flowPreviewAgent, setFlowPreviewAgent] = useState(null);
  const [flowPreview, setFlowPreview] = useState(null);
  const [flowPreviewLoading, setFlowPreviewLoading] = useState(false);
  const [flowPreviewError, setFlowPreviewError] = useState('');
  const [flowPreviewReadOnly, setFlowPreviewReadOnly] = useState(false);
  const [qaAgent, setQaAgent] = useState(null);
  const [scrapeAgent, setScrapeAgent] = useState(null);
  const [scrapeUrl, setScrapeUrl] = useState('');
  const [scrapeJob, setScrapeJob] = useState(null);
  const [scrapeDraft, setScrapeDraft] = useState(null);
  const [summaryModalDraft, setSummaryModalDraft] = useState(null);
  const [scrapeDraftHistory, setScrapeDraftHistory] = useState([]);

  const [scrapeHistoryLoading, setScrapeHistoryLoading] = useState(false);
  const [scrapeStatus, setScrapeStatus] = useState('');
  const [smallestGenderFilter, setSmallestGenderFilter] = useState('all');
  const [scrapeError, setScrapeError] = useState('');
  const [scrapeLoading, setScrapeLoading] = useState(false);
  const [scrapeApplyLoading, setScrapeApplyLoading] = useState(false);
  const [scrapePreflightLoading, setScrapePreflightLoading] = useState(false);
  const [scrapeApplyMessage, setScrapeApplyMessage] = useState('');
  
  const [formData, setFormData] = useState(makeInitialFormData);

  const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

  const fetchAgents = async () => {
    setLoading(true);
    try {
      const ownAgentsQuery = user?.role === 'client' && user?.email
        ? `?user_email=${encodeURIComponent(user.email)}`
        : '';
      const res = await fetch(`${API}/api/agents${ownAgentsQuery}`);
      if (res.ok) {
        const data = await res.json();
        setAgents(Array.isArray(data) ? data : []);
      }
    } catch (err) {
      console.error('Failed to fetch agents', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchAgents();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const openCreateModal = () => {
    setModalMode('create');
    setEditingAgentId(null);
    setFormData(makeInitialFormData());
    setShowModal(true);
  };

  const openEditModal = (agent) => {
    setModalMode('edit');
    setEditingAgentId(agent.id);
    setFormData(formDataFromAgent(agent));
    setShowModal(true);
  };

  const closeModal = () => {
    setShowModal(false);
    setEditingAgentId(null);
    setModalMode('create');
  };

  const closeFlowPreview = () => {
    setFlowPreviewAgent(null);
    setFlowPreview(null);
    setFlowPreviewError('');
    setFlowPreviewLoading(false);
    setFlowPreviewReadOnly(false);
  };

  const openScrapeModal = (agent) => {
    setScrapeAgent(agent);
    setScrapeUrl('');
    setScrapeJob(null);
    setScrapeDraft(null);
    setSummaryModalDraft(null);
    setScrapeDraftHistory([]);
    setScrapeHistoryLoading(false);
    setScrapeStatus('');
    setScrapeError('');
    setScrapeApplyMessage('');
    setScrapeApplyLoading(false);
    setScrapePreflightLoading(false);
    setScrapeLoading(false);
    loadScrapeDraftHistory(agent);
  };

  const closeScrapeModal = () => {
    if (scrapeLoading) return;
    setScrapeAgent(null);
    setScrapeUrl('');
    setScrapeJob(null);
    setScrapeDraft(null);
    setSummaryModalDraft(null);
    setScrapeDraftHistory([]);
    setScrapeHistoryLoading(false);
    setScrapeStatus('');
    setScrapeError('');
    setScrapeApplyMessage('');
    setScrapeApplyLoading(false);
    setScrapePreflightLoading(false);
  };

  const buildTenantHeaders = (json = false) => {
    const headers = json ? { 'Content-Type': 'application/json' } : {};
    if (user?.clientId) headers['X-Tenant-ID'] = user.clientId;
    if (user?.email) headers['X-User-Email'] = user.email;
    return headers;
  };

  const selectedScrapeClientId = (agent = scrapeAgent) => (
    user?.clientId || agent?.client_id || (user?.role === 'admin' ? activeClient : null)
  );

  const loadScrapeDraftHistory = async (agent) => {
    if (!agent?.id || !SCRAPE_GENERATE_SCRIPT_ENABLED) return;
    setScrapeHistoryLoading(true);
    try {
      const params = new URLSearchParams({ agentId: agent.id });
      const clientId = selectedScrapeClientId(agent);
      if (clientId) {
        params.set('clientId', clientId);
      }
      const res = await fetch(`${API}/api/intelligence/script-drafts?${params.toString()}`, {
        headers: buildTenantHeaders(),
      });
      if (!res.ok) return;
      const body = await res.json();
      setScrapeDraftHistory(Array.isArray(body.items) ? body.items : []);
    } catch (err) {
      console.error('Failed to load generated drafts', err);
    } finally {
      setScrapeHistoryLoading(false);
    }
  };

  const openFlowPreview = async (agent) => {
    setFlowPreviewAgent(agent);
    setFlowPreview(null);
    setFlowPreviewError('');
    setFlowPreviewLoading(true);
    setFlowPreviewReadOnly(false);
    try {
      const headers = user?.clientId ? { 'X-Tenant-ID': user.clientId } : {};
      const res = await fetch(`${API}/api/agents/${agent.id}/flow-preview`, { headers });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || 'Flow preview is unavailable');
      }
      setFlowPreview(await res.json());
    } catch (err) {
      setFlowPreviewError(err.message || 'Flow preview is unavailable');
    } finally {
      setFlowPreviewLoading(false);
    }
  };

  const saveFlowDraft = async (draft) => {
    if (!flowPreviewAgent) return;
    const headers = { 'Content-Type': 'application/json' };
    if (user?.clientId) headers['X-Tenant-ID'] = user.clientId;
    const res = await fetch(`${API}/api/agents/${flowPreviewAgent.id}/flow-v2-draft`, {
      method: 'PUT',
      headers,
      body: JSON.stringify(draft)
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(body.detail || 'Flow save failed');
    }
    setFlowPreview(body);
  };

  const parseApiError = async (res, fallback) => {
    const body = await res.json().catch(() => ({}));
    return body.detail || fallback;
  };

  const pollScrapeJob = async (jobId) => {
    let latest = null;
    for (let attempt = 0; attempt < SCRAPE_POLL_ATTEMPTS; attempt += 1) {
      await wait(SCRAPE_POLL_INTERVAL_MS);
      const res = await fetch(`${API}/api/intelligence/scrape-jobs/${jobId}`, {
        headers: buildTenantHeaders(),
      });
      if (!res.ok) break;
      latest = await res.json();
      setScrapeJob(latest);
      setScrapeStatus(`Scrape ${latest.status || 'queued'}`);
      if (['completed', 'failed', 'draft_ready', 'cancelled'].includes(latest.status)) break;
    }
    return latest;
  };

  const handleGenerateFromWebsite = async (event) => {
    event.preventDefault();
    if (!scrapeAgent || !scrapeUrl.trim()) return;
    setScrapeLoading(true);
    setScrapeError('');
    setScrapeDraft(null);
    setScrapeJob(null);
    try {
      const scrapeClientId = selectedScrapeClientId(scrapeAgent);
      setScrapeStatus('Creating scrape job');
      const jobRes = await fetch(`${API}/api/intelligence/scrape-jobs`, {
        method: 'POST',
        headers: buildTenantHeaders(true),
        body: JSON.stringify({
          url: scrapeUrl.trim(),
          agentId: scrapeAgent.id,
          clientId: scrapeClientId,
          requestedBy: user?.email || '',
          reuseExisting: true,
        }),
      });
      if (!jobRes.ok) {
        throw new Error(await parseApiError(jobRes, 'Scrape job could not be created'));
      }
      const job = await jobRes.json();
      setScrapeJob(job);
      const reusedJob = Boolean(job.cache?.reused);
      if (reusedJob) {
        setScrapeStatus(`Using cached ${job.status || 'queued'} scrape job`);
      }

      if (SCRAPE_WORKER_V1_ENABLED && !SCRAPE_REUSE_FINAL_STATUSES.includes(job.status)) {
        setScrapeStatus('Dispatching scrape worker');
        const dispatchRes = await fetch(`${API}/api/intelligence/scrape-jobs/${job.id}/dispatch`, {
          method: 'POST',
          headers: buildTenantHeaders(true),
          body: JSON.stringify({
            industryHint: scrapeAgent.agent_type || DEFAULT_AGENT_TYPE,
            requestedBy: user?.email || '',
          }),
        });
        if (!dispatchRes.ok) {
          throw new Error(await parseApiError(dispatchRes, 'Scrape worker could not be dispatched'));
        }
        await dispatchRes.json().catch(() => ({}));
        const latest = await pollScrapeJob(job.id);
        if (latest?.status === 'failed') {
          throw new Error(latest.error || 'Scrape worker failed');
        }
        if (latest?.status === 'cancelled') {
          throw new Error(latest.error || 'Scrape job was cancelled');
        }
        if (!SCRAPE_REUSE_FINAL_STATUSES.includes(latest?.status)) {
          throw new Error('Scrape job is still running. Please wait a moment and refresh the draft history.');
        }
      }

      setScrapeStatus('Creating draft');
      const draftRes = await fetch(`${API}/api/intelligence/script-drafts`, {
        method: 'POST',
        headers: buildTenantHeaders(true),
        body: JSON.stringify({
          jobId: job.id,
          agentId: scrapeAgent.id,
          industryHint: scrapeAgent.agent_type || DEFAULT_AGENT_TYPE,
        }),
      });
      if (!draftRes.ok) {
        throw new Error(await parseApiError(draftRes, 'Script draft could not be created'));
      }
      const draft = await draftRes.json();
      setScrapeDraft(draft);
      setScrapeDraftHistory((items) => [
        draft,
        ...items.filter((item) => item.id !== draft.id),
      ]);
      setScrapeStatus('Draft ready');
    } catch (err) {
      setScrapeError(err.message || 'Website script generation failed');
      setScrapeStatus('');
    } finally {
      setScrapeLoading(false);
    }
  };

  const handleApplyGeneratedDraft = async (draftToApply = scrapeDraft) => {
    if (!draftToApply || !scrapeAgent) return;
    setScrapeApplyLoading(true);
    setScrapeError('');
    setScrapeApplyMessage('');
    try {
      const res = await fetch(`${API}/api/intelligence/script-drafts/${draftToApply.id}/apply-flow-draft`, {
        method: 'POST',
        headers: buildTenantHeaders(true),
        body: JSON.stringify({
          reviewAcknowledged: true,
          reviewNotes: 'Saved from agents generate script review modal',
        }),
      });
      if (!res.ok) {
        throw new Error(await parseApiError(res, 'Generated draft could not be applied'));
      }
      const preview = await res.json();
      setScrapeApplyMessage('Review recorded. Draft saved to agent flow.');
      setFlowPreviewAgent(scrapeAgent);
      setFlowPreview(preview);
      setFlowPreviewError('');
      setFlowPreviewLoading(false);
      setFlowPreviewReadOnly(false);
      setScrapeAgent(null);
      setScrapeUrl('');
      setScrapeJob(null);
      setScrapeDraft(null);
      setScrapeStatus('');
      setScrapeError('');
    } catch (err) {
      setScrapeError(err.message || 'Generated draft could not be applied');
    } finally {
      setScrapeApplyLoading(false);
    }
  };

  const handlePreflightGeneratedDraft = async (draftToPreflight = scrapeDraft) => {
    if (!draftToPreflight || !scrapeAgent || !FLOW_VISUALIZATION_ENABLED) return;
    setScrapePreflightLoading(true);
    setScrapeError('');
    setScrapeApplyMessage('');
    setFlowPreviewLoading(true);
    setFlowPreviewError('');
    try {
      const res = await fetch(`${API}/api/intelligence/script-drafts/${draftToPreflight.id}/preflight-flow-draft`, {
        method: 'POST',
        headers: buildTenantHeaders(),
      });
      if (!res.ok) {
        throw new Error(await parseApiError(res, 'Generated draft preflight failed'));
      }
      const preview = await res.json();
      setFlowPreviewAgent({
        ...scrapeAgent,
        id: preview.agent?.id || scrapeAgent.id,
        name: preview.agent?.name || scrapeAgent.name,
      });
      setFlowPreview(preview);
      setFlowPreviewReadOnly(true);
      setScrapeApplyMessage('Preflight passed. No flow draft was saved.');
    } catch (err) {
      setFlowPreviewError(err.message || 'Generated draft preflight failed');
      setScrapeError(err.message || 'Generated draft preflight failed');
    } finally {
      setFlowPreviewLoading(false);
      setScrapePreflightLoading(false);
    }
  };
  const handleFileUpload = (e) => {
    const file = e.target.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (event) => {
      try {
        const json = JSON.parse(event.target.result);
        setFormData({ ...formData, custom_json: json });
        alert("Custom JSON loaded successfully.");
      } catch (err) {
        alert("Invalid JSON file. Please check the format.");
      }
    };
    reader.readAsText(file);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    
    // Auto-generate name based on Agent Type and Voice if empty
    let autoName = formData.name;
    if (!autoName || autoName.trim() === '') {
      const agentTypeName = AGENT_TYPE_TEMPLATES[formData.agent_type]?.label || 'Voice Agent';
      let voiceName = 'Default Voice';
      /* if (formData.tts_provider === 'cartesia') {
         const cartesiaVoice = CARTESIA_FEMALE_VOICES.find(v => v.value === formData.cartesia_voice_id);
         if (cartesiaVoice) voiceName = cartesiaVoice.label.split('-')[0].trim();
      } else if (formData.voice === '11labs-06nek6zjTCD1vCbtc8bc') {
         voiceName = 'Priya';
      } */

          if (formData.tts_provider === 'cartesia') {
         const cartesiaVoice = CARTESIA_FEMALE_VOICES.find(v => v.value === formData.cartesia_voice_id);
         if (cartesiaVoice) voiceName = cartesiaVoice.label.split('-')[0].trim();
      } else if (formData.tts_provider === 'magpie') { // <--- ADD THIS BLOCK
         voiceName = formData.magpie_speaker || 'Sofia';
      } else if (formData.voice === '11labs-06nek6zjTCD1vCbtc8bc') {
         voiceName = 'Priya';
      } 
      autoName = `${agentTypeName} - ${voiceName}`;
    }

      // Format the Magpie voice tag (e.g. Magpie-Multilingual.HI-IN.Sofia)
    let voicePayload = formData.voice;
    if (formData.tts_provider === 'magpie') {
      const isHindi = (formData.language || '').toLowerCase().includes('hindi');
      const locale = isHindi ? 'HI-IN' : 'EN-US';
      const speaker = formData.magpie_speaker || DEFAULT_MAGPIE_VOICE;
      voicePayload = `Magpie-Multilingual.${locale}.${speaker}`;
    }

    const payload = {
      ...formData,
      name: autoName,
      data_fields: formData.data_fields.split(',').map(s => s.trim()).filter(Boolean)
    };
    const isEdit = modalMode === 'edit' && editingAgentId;
    const url = isEdit ? `${API}/api/agents/${editingAgentId}` : `${API}/api/agents`;

    try {
      const res = await fetch(url, {
        method: isEdit ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (res.ok) {
        closeModal();
        setFormData(makeInitialFormData({
          agent_type: formData.agent_type,
          script: AGENT_TYPE_TEMPLATES[formData.agent_type]?.prompt || AGENT_TYPE_TEMPLATES[DEFAULT_AGENT_TYPE].prompt,
          data_fields: AGENT_TYPE_TEMPLATES[formData.agent_type]?.fields || AGENT_TYPE_TEMPLATES[DEFAULT_AGENT_TYPE].fields
        }));
        fetchAgents();
      } else {
        alert(isEdit ? "Failed to update agent" : "Failed to create agent");
      }
    } catch (e) {
      console.error(e);
      alert("Error connecting to backend");
    }
  };

  const handleDeleteAgent = async (agentId) => {
    if (!window.confirm("Are you sure you want to delete this agent?")) {
      return;
    }
    
    try {
      const res = await fetch(`${API}/api/agents/${agentId}`, {
        method: 'DELETE',
      });
      if (res.ok) {
        fetchAgents();
      } else {
        alert("Failed to delete agent");
      }
    } catch (e) {
      console.error(e);
      alert("Error connecting to backend");
    }
  };

  const handleAgentTypeChange = (agentType) => {
    const template = AGENT_TYPE_TEMPLATES[agentType] || AGENT_TYPE_TEMPLATES[DEFAULT_AGENT_TYPE];
    setFormData({
      ...formData,
      agent_type: agentType,
      script: template.prompt,
      data_fields: template.fields
    });
  };

  return (
    <DashboardLayout>
      <div className="d-flex justify-content-between align-items-center mb-4">
        <div>
          <h2 className="h4 fw-bold mb-1">Voice Agents</h2>
          <p className="text-muted small mb-0">Configure AI agent personas and voices</p>
        </div>
        {user?.role === 'admin' && (
          <button className="btn btn-primary btn-sm px-3 shadow-sm" onClick={openCreateModal}>
            + Create Agent
          </button>
        )}
      </div>
      
      {loading ? (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '80px' }}>
          <div className="cc-spinner" />
        </div>
      ) : agents.length === 0 ? (
        <div style={{ background: '#141414', border: '1px solid #262626', borderRadius: '12px' }}>
          <div className="cc-empty-state" style={{ minHeight: '200px' }}>
            <Bot size={32} strokeWidth={1.5} style={{ color: '#3D3D3D' }} />
            <span style={{ fontWeight: 500 }}>No Agents Configured</span>
            <span style={{ fontSize: '12px', color: '#6B6B6B' }}>Click &apos;Create Agent&apos; to build your first AI persona.</span>
          </div>
        </div>
      ) : (
        <div className="row g-4">
          {agents.map((agent, index) => (
            <div key={agent.id || index} className="col-md-4">
              <div style={{ background: '#141414', border: '1px solid #262626', borderRadius: '12px', display: 'flex', flexDirection: 'column', height: '100%' }}>
                <div style={{ padding: '20px' }}>
                  <div className="d-flex justify-content-between align-items-start mb-3">
                    <h5 style={{ fontSize: '15px', fontWeight: 600, color: '#FFFFFF', margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <Bot size={18} strokeWidth={1.5} style={{ color: '#6B6B6B' }} />
                      {agent.name || 'Unnamed Agent'}
                    </h5>
                    <div style={{ display: 'flex', gap: '6px' }}>
                      <span style={{ padding: '2px 8px', border: `1px solid ${agent.certification_status === 'Certified' ? '#4ADE80' : '#3D3D3D'}`, borderRadius: '4px', fontSize: '11px', color: agent.certification_status === 'Certified' ? '#4ADE80' : '#6B6B6B', background: 'transparent', fontWeight: 500 }}>
                        {agent.certification_status || 'Draft'}
                      </span>
                      <span style={{ padding: '2px 8px', border: '1px solid #262626', borderRadius: '4px', fontSize: '11px', color: '#A3A3A3', background: 'transparent' }}>{agent.language || 'English'}</span>
                    </div>
                  </div>
                  <p style={{ fontSize: '12px', color: '#A3A3A3', marginBottom: '6px' }}><strong style={{ color: '#6B6B6B' }}>Voice:</strong> {agent.voice || 'Default'}</p>
                  <p style={{ fontSize: '12px', color: '#A3A3A3', marginBottom: '6px' }}><strong style={{ color: '#6B6B6B' }}>Provider:</strong> {getProviderLabel('telephony', agent.provider || 'twilio')}</p>
                  <p style={{ fontSize: '12px', color: '#A3A3A3', marginBottom: '6px' }}><strong style={{ color: '#6B6B6B' }}>Type:</strong> {AGENT_TYPE_TEMPLATES[agent.agent_type]?.label || agent.agent_type || 'Real Estate Sales'}</p>
                  <p style={{ fontSize: '12px', color: '#A3A3A3', marginBottom: '6px' }}><strong style={{ color: '#6B6B6B' }}>Assigned:</strong> {agent.client_name ? `${agent.client_name} (${agent.assigned_email})` : agent.assigned_email || 'Unassigned'}</p>
                  <p style={{ fontSize: '12px', color: '#A3A3A3', marginBottom: '6px' }}><strong style={{ color: '#6B6B6B' }}>STT:</strong> {getProviderLabel('stt', agent.stt_provider || 'groq')}</p>
                  <p style={{ fontSize: '12px', color: '#A3A3A3', marginBottom: '10px' }}><strong style={{ color: '#6B6B6B' }}>TTS:</strong> {getProviderLabel('tts', agent.tts_provider || 'edge')}</p>
                  {agent.tts_provider === 'cartesia' && (
                    <p style={{ fontSize: '12px', color: '#A3A3A3', marginBottom: '10px' }}><strong style={{ color: '#6B6B6B' }}>Premium Voice:</strong> {CARTESIA_FEMALE_VOICES.find(v => v.value === agent.cartesia_voice_id)?.label || agent.cartesia_voice_id || 'Hinglish Speaking Lady'}</p>
                  )}

                  {/* ADD THIS MAGPIE DISPLAY */}
                  {agent.tts_provider === 'magpie' && (
                    <p style={{ fontSize: '12px', color: '#A3A3A3', marginBottom: '10px' }}><strong style={{ color: '#6B6B6B' }}>NVIDIA Voice:</strong> {agent.voice?.replace('Magpie-Multilingual.', '') || 'Sofia (English/Hindi)'}</p>
                  )}

                  <div style={{ fontSize: '12px', marginTop: '8px' }}>
                    <strong style={{ display: 'block', marginBottom: '6px', color: '#6B6B6B', fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Extracted Fields:</strong>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                      {agent.data_fields?.map((field, i) => (
                        <span key={i} style={{ padding: '2px 8px', border: '1px solid #262626', borderRadius: '4px', fontSize: '11px', color: '#A3A3A3', background: 'transparent' }}>{field}</span>
                      ))}
                    </div>
                  </div>
                </div>
              
                    <div
                      style={{
                        marginTop: 'auto',
                        padding: '12px 20px',
                        borderTop: '1px solid #262626',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        gap: '10px',
                      }}
                    >
                      {/* ID */}
                      <small
                        style={{
                          color: '#6B6B6B',
                          fontSize: '11px',
                          fontFamily: 'monospace',
                          flexShrink: 0,
                          width: '62px',
                        }}
                      >
                        ID: {(agent.id || agent.agent_id || '').substring(0, 8)}...
                      </small>

                      {/* Buttons */}
                      <div
                        style={{
                          display: 'flex',
                          flexDirection: 'column',
                          alignItems: 'flex-end',
                          gap: '5px',
                          flex: 1,
                        }}
                      >
                        {/* ROW 1 — 2 BUTTONS */}
                        <div
                          style={{
                            display: 'flex',
                            justifyContent: 'flex-end',
                            alignItems: 'center',
                            gap: '5px',
                            width: '100%',
                          }}
                        >
                          {FLOW_VISUALIZATION_ENABLED && (
                            <button
                              type="button"
                              className="btn btn-sm"
                              style={{
                                background: '#111111',
                                color: '#FFFFFF',
                                border: 'none',
                                fontWeight: 700,
                                fontSize: '14px',
                                borderRadius: '8px',
                                boxShadow: 'none',
                                padding: '8px 14px',
                                transition: 'all 0.2s ease',
                                whiteSpace: 'nowrap',
                              }}
                              onMouseEnter={(e) => {
                                e.currentTarget.style.background = '#FFFFFF';
                                e.currentTarget.style.color = '#111111';
                              }}
                              onMouseLeave={(e) => {
                                e.currentTarget.style.background = '#111111';
                                e.currentTarget.style.color = '#FFFFFF';
                              }}
                              onClick={() => openFlowPreview(agent)}
                            >
                              Flow
                            </button>
                          )}

                          {SCRAPE_GENERATE_SCRIPT_ENABLED && user?.role === 'admin' && (
                            <button
                              type="button"
                              className="btn btn-sm"
                              style={{
                                background: '#111111',
                                color: '#FFFFFF',
                                border: 'none',
                                fontWeight: 700,
                                fontSize: '14px',
                                borderRadius: '8px',
                                boxShadow: 'none',
                                padding: '8px 14px',
                                transition: 'all 0.2s ease',
                                whiteSpace: 'nowrap',
                              }}
                              onMouseEnter={(e) => {
                                e.currentTarget.style.background = '#FFFFFF';
                                e.currentTarget.style.color = '#111111';
                              }}
                              onMouseLeave={(e) => {
                                e.currentTarget.style.background = '#111111';
                                e.currentTarget.style.color = '#FFFFFF';
                              }}
                              onClick={() => openScrapeModal(agent)}
                            >
                              Generate Script
                            </button>
                          )}
                        </div>

                        {/* ROW 2 — 3 BUTTONS */}
                        {user?.role === 'admin' && (
                          <div
                            style={{
                              display: 'flex',
                              justifyContent: 'flex-end',
                              alignItems: 'center',
                              gap: '5px',
                              width: '100%',
                            }}
                          >
                            {/* QA TEST */}
                            <button
                              type="button"
                              className="btn btn-sm"
                              style={{
                                background: '#111111',
                                color: '#FFFFFF',
                                border: 'none',
                                fontWeight: 700,
                                fontSize: '14px',
                                borderRadius: '8px',
                                boxShadow: 'none',
                                padding: '8px 14px',
                                transition: 'all 0.2s ease',
                                whiteSpace: 'nowrap',
                              }}
                              onMouseEnter={(e) => {
                                e.currentTarget.style.background = '#FFFFFF';
                                e.currentTarget.style.color = '#111111';
                              }}
                              onMouseLeave={(e) => {
                                e.currentTarget.style.background = '#111111';
                                e.currentTarget.style.color = '#FFFFFF';
                              }}
                              onClick={() => setQaAgent(agent)}
                            >
                              QA Test
                            </button>

                            {/* EDIT */}
                            <button
                              type="button"
                              className="btn btn-sm"
                              style={{
                                background: '#111111',
                                color: '#FFFFFF',
                                border: 'none',
                                fontWeight: 700,
                                fontSize: '14px',
                                borderRadius: '8px',
                                boxShadow: 'none',
                                padding: '8px 14px',
                                transition: 'all 0.2s ease',
                                whiteSpace: 'nowrap',
                              }}
                              onMouseEnter={(e) => {
                                e.currentTarget.style.background = '#FFFFFF';
                                e.currentTarget.style.color = '#111111';
                              }}
                              onMouseLeave={(e) => {
                                e.currentTarget.style.background = '#111111';
                                e.currentTarget.style.color = '#FFFFFF';
                              }}
                              onClick={() => openEditModal(agent)}
                            >
                              Edit
                            </button>

                            {/* DELETE */}
                            <button
                              type="button"
                              className="btn btn-sm"
                              style={{
                                background: '#111111',
                                color: '#FFFFFF',
                                border: 'none',
                                fontWeight: 700,
                                fontSize: '14px',
                                borderRadius: '8px',
                                boxShadow: 'none',
                                padding: '8px 14px',
                                transition: 'all 0.2s ease',
                                whiteSpace: 'nowrap',
                              }}
                              onMouseEnter={(e) => {
                                e.currentTarget.style.background = '#FFFFFF';
                                e.currentTarget.style.color = '#111111';
                              }}
                              onMouseLeave={(e) => {
                                e.currentTarget.style.background = '#111111';
                                e.currentTarget.style.color = '#FFFFFF';
                              }}
                              onClick={() =>
                                handleDeleteAgent(agent.id || agent.agent_id)
                              }
                            >
                              Delete
                            </button>
                          </div>
                        )}
                      </div>
                    </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {flowPreviewAgent && (
        <FlowPreviewModal
          agent={flowPreviewAgent}
          preview={flowPreview}
          loading={flowPreviewLoading}
          error={flowPreviewError}
          onClose={closeFlowPreview}
          onSave={!flowPreviewReadOnly && user?.role === 'admin' ? saveFlowDraft : null}
        />
      )}

      {qaAgent && (
        <QATestModal 
          agent={qaAgent} 
          onClose={() => setQaAgent(null)} 
          API={API} 
          headers={buildTenantHeaders(true)}
          onCertifySuccess={fetchAgents}
        />
      )}

      {scrapeAgent && (
        <div className="modal show d-block scrape-modal" tabIndex="-1" style={{ backgroundColor: 'rgba(0,0,0,0.8)', backdropFilter: 'blur(4px)' }}>
          <div className="modal-dialog modal-xl modal-dialog-centered modal-dialog-scrollable">
            <div className="modal-content" style={{ background: '#141414', border: '1px solid #262626', borderRadius: '16px' }}>
              {/* ── Header ── */}
              <div className="modal-header" style={{ borderBottom: '1px solid #262626', padding: '16px 24px' }}>
                <div className="d-flex align-items-center gap-3">
                  <div style={{ background: '#1E1E1E', border: '1px solid #3D3D3D', borderRadius: '10px', padding: '8px 10px', fontSize: '1.3rem' }}>🌐</div>
                  <div>
                    <h5 className="modal-title fw-bold mb-0" style={{ color: '#FFFFFF', fontSize: '16px' }}>Generate Script from Website</h5>
                    <div style={{ fontSize: '12px', color: '#6B6B6B', marginTop: '2px' }}>Agent: {scrapeAgent.name || 'Voice Agent'}</div>
                  </div>
                </div>
                <button type="button" className="btn-close btn-close-white shadow-none" onClick={closeScrapeModal} disabled={scrapeLoading}></button>
              </div>

              <div className="modal-body" style={{ background: '#0A0A0A', padding: '24px' }}>

                {/* ── URL Input ── */}
                <div style={{ background: '#141414', border: '1px solid #262626', borderRadius: '12px', padding: '20px', marginBottom: '24px' }}>
                  <form onSubmit={handleGenerateFromWebsite}>
                    <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, color: '#A3A3A3', marginBottom: '12px' }}>
                      🔗 Paste a website URL to extract company info &amp; generate a voice agent script
                    </label>
                    <div className="d-flex gap-2">
                      <input
                        type="url"
                        style={{ flex: 1, background: '#1E1E1E', border: '1px solid #3D3D3D', borderRadius: '8px', padding: '10px 14px', color: '#FFFFFF', fontSize: '14px', outline: 'none' }}
                        required
                        value={scrapeUrl}
                        onChange={(e) => setScrapeUrl(e.target.value)}
                        placeholder="https://yourcompany.com"
                        disabled={scrapeLoading}
                      />
                      <button
                        type="submit"
                        style={{ background: '#FFFFFF', color: '#0A0A0A', border: 'none', borderRadius: '8px', padding: '0 20px', fontWeight: 600, fontSize: '14px', minWidth: '140px', cursor: scrapeLoading || !scrapeUrl.trim() ? 'not-allowed' : 'pointer', opacity: scrapeLoading || !scrapeUrl.trim() ? 0.5 : 1 }}
                        disabled={scrapeLoading || !scrapeUrl.trim()}
                      >
                        {scrapeLoading ? (
                          <span className="d-flex align-items-center justify-content-center gap-2">
                            <span className="scrape-loading-dot"></span>
                            <span className="scrape-loading-dot"></span>
                            <span className="scrape-loading-dot"></span>
                          </span>
                        ) : '⚡ Analyze Site'}
                      </button>
                    </div>
                    {scrapeLoading && (
                      <div className="mt-3">
                        <div className="d-flex justify-content-between align-items-center mb-1">
                          <small style={{ color: '#FFFFFF', fontWeight: 500 }}>{scrapeStatus || 'Analyzing website…'}</small>
                          <small style={{ color: '#6B6B6B' }}>This may take up to 30s</small>
                        </div>
                        <div className="scrape-progress-bar" style={{ marginTop: '8px', background: '#262626' }}>
                          <div className="scrape-progress-fill" style={{ background: '#FFFFFF' }}></div>
                        </div>
                      </div>
                    )}
                  </form>
                </div>

                {/* ── Alerts ── */}
                {scrapeError && (
                  <div style={{ background: 'rgba(248,113,113,0.08)', border: '1px solid #F87171', color: '#F87171', borderRadius: '8px', padding: '12px 16px', fontSize: '13px', marginBottom: '24px' }}>
                    ⚠️ {scrapeError}
                  </div>
                )}
                {scrapeApplyMessage && (
                  <div style={{ background: 'rgba(74,222,128,0.08)', border: '1px solid #4ADE80', color: '#4ADE80', borderRadius: '8px', padding: '12px 16px', fontSize: '13px', marginBottom: '24px' }}>
                    ✅ {scrapeApplyMessage}
                  </div>
                )}

                {/* ── Website Summary Panel ── */}
                {scrapeDraft && scrapeDraft.knowledge && (() => {
                  const k = scrapeDraft.knowledge;
                  const quality = draftQuality(scrapeDraft);
                  const companyName = k.company?.name || k.domain || 'Unknown Company';
                  const domain = k.domain || '';
                  const industry = String(k.industry || 'unknown').replaceAll('_', ' ');
                  const services = k.products_or_services || [];
                  const valueProps = k.value_propositions || [];
                  const faqs = k.faqs || [];
                  const objections = k.objections || [];
                  const qualQuestions = k.qualification_questions || [];
                  const pagesCrawled = k.pages_crawled || [];
                  const primaryPages = k.content_inventory?.primary_pages || [];
                  const sourceUrl = k.source_url || scrapeUrl;
                  return (
                    <>
                      {/* Hero Card */}
                      <div style={{ background: '#141414', border: '1px solid #262626', borderRadius: '12px', padding: '24px', marginBottom: '24px' }}>
                        <div className="d-flex justify-content-between align-items-start gap-3">
                          <div className="flex-grow-1">
                            <div className="d-flex align-items-center gap-2 mb-1">
                              <span style={{ fontSize: '1.5rem' }}>🏢</span>
                              <div style={{ fontSize: '18px', fontWeight: 600, color: '#FFFFFF' }}>{companyName}</div>
                            </div>
                            <div style={{ fontSize: '13px', color: '#A3A3A3', fontFamily: 'monospace', marginBottom: '16px' }}>{domain || sourceUrl}</div>
                            <div className="d-flex flex-wrap gap-2 align-items-center">
                              <span style={{ padding: '4px 12px', border: '1px solid #3D3D3D', borderRadius: '20px', fontSize: '11px', fontWeight: 500, color: '#E5E5E5', textTransform: 'capitalize' }}>
                                🏭 {industry}
                              </span>
                              {k.content_inventory?.has_services && (
                                <span style={{ padding: '4px 12px', border: '1px solid #3D3D3D', borderRadius: '20px', fontSize: '11px', color: '#E5E5E5' }}>
                                  ✅ Services listed
                                </span>
                              )}
                              {k.content_inventory?.has_faq && (
                                <span style={{ padding: '4px 12px', border: '1px solid #3D3D3D', borderRadius: '20px', fontSize: '11px', color: '#E5E5E5' }}>
                                  ❓ FAQs found
                                </span>
                              )}
                              {k.content_inventory?.has_contact && (
                                <span style={{ padding: '4px 12px', border: '1px solid #3D3D3D', borderRadius: '20px', fontSize: '11px', color: '#E5E5E5' }}>
                                  📞 Contact page
                                </span>
                              )}
                              <a href={sourceUrl} target="_blank" rel="noreferrer" style={{ padding: '4px 12px', border: '1px solid #3D3D3D', borderRadius: '20px', fontSize: '11px', color: '#FFFFFF', textDecoration: 'none', background: '#1E1E1E' }}>
                                🔗 Visit Site ↗
                              </a>
                            </div>
                          </div>
                          {quality && (
                            <div className="text-center flex-shrink-0">
                              <div className={`ws-score-ring ${quality.level}`} style={{ background: '#1E1E1E' }}>
                                <div>{quality.score}</div>
                                <div style={{ fontSize: '0.55rem', fontWeight: 500, marginTop: '-2px' }}>/100</div>
                              </div>
                              <div style={{ fontSize: '10px', color: '#A3A3A3', marginTop: '6px', textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 600 }}>
                                {quality.level}
                              </div>
                            </div>
                          )}
                        </div>
                        {/* Draft meta */}
                        <div className="d-flex flex-wrap gap-4 mt-4 pt-4" style={{ borderTop: '1px solid #262626' }}>
                          <div style={{ fontSize: '12px' }}>
                            <span style={{ color: '#6B6B6B' }}>Draft ID: </span>
                            <span style={{ fontFamily: 'monospace', color: '#FFFFFF', fontWeight: 500 }}>{(scrapeDraft.id || '').substring(0, 8)}…</span>
                          </div>
                          <div style={{ fontSize: '12px' }}>
                            <span style={{ color: '#6B6B6B' }}>Pages crawled: </span>
                            <span style={{ fontWeight: 500, color: '#FFFFFF' }}>{pagesCrawled.length}</span>
                          </div>
                          <div style={{ fontSize: '12px' }}>
                            <span style={{ color: '#6B6B6B' }}>Flow nodes: </span>
                            <span style={{ fontWeight: 500, color: '#FFFFFF' }}>{scrapeDraft.draft?.nodes?.length || 0}</span>
                          </div>
                          <div style={{ fontSize: '12px' }}>
                            <span style={{ color: '#6B6B6B' }}>Mode: </span>
                            <span style={{ fontWeight: 500, color: '#FFFFFF', textTransform: 'capitalize' }}>{scrapeDraft.draft?.runtime_mode || 'shadow'}</span>
                          </div>
                        </div>
                      </div>

                      {/* ── Main Summary Grid ── */}
                      <div className="row g-3 mb-3">

                        {/* Products / Services */}
                        {services.length > 0 && (
                          <div className="col-md-6">
                            <div className="ws-section">
                              <div className="ws-section-title">Products &amp; Services</div>
                              <div className="d-flex flex-wrap gap-1">
                                {services.slice(0, 8).map((item, i) => (
                                  <span key={i} className="ws-pill">{item.name}</span>
                                ))}
                              </div>
                            </div>
                          </div>
                        )}

                        {/* Value Propositions */}
                        {valueProps.length > 0 && (
                          <div className="col-md-6">
                            <div className="ws-section">
                              <div className="ws-section-title">Value Propositions</div>
                              {valueProps.slice(0, 4).map((vp, i) => (
                                <div key={i} className="ws-value-item">
                                  <span className="ws-value-icon">✓</span>
                                  <span>{vp.text}</span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        {/* Qualification Questions */}
                        {qualQuestions.length > 0 && (
                          <div className="col-md-6">
                            <div className="ws-section">
                              <div className="ws-section-title">Qualification Questions</div>
                              {qualQuestions.map((q, i) => (
                                <div key={i} className="ws-faq-item">
                                  <span style={{ color: '#6366f1', fontWeight: 600, marginRight: '6px' }}>Q{i + 1}.</span>
                                  {q}
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        {/* FAQs */}
                        {faqs.length > 0 && (
                          <div className="col-md-6">
                            <div className="ws-section">
                              <div className="ws-section-title">FAQs Detected</div>
                              {faqs.slice(0, 5).map((faq, i) => (
                                <div key={i} className="ws-faq-item">
                                  <div className="ws-faq-q">❓ {faq.question}</div>
                                  {faq.answer && <div className="mt-1" style={{ color: '#6b7280' }}>{faq.answer}</div>}
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        {/* Objection Handling */}
                        {objections.length > 0 && (
                          <div className="col-md-6">
                            <div className="ws-section">
                              <div className="ws-section-title">Objection Handling</div>
                              <div className="d-flex flex-column gap-2">
                                {objections.map((obj, i) => (
                                  <div key={i} style={{ background: '#1E1E1E', border: '1px solid #3D3D3D', borderRadius: '8px', padding: '8px 12px' }}>
                                    <div style={{ fontSize: '11px', fontWeight: 600, color: '#E5E5E5', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '4px' }}>
                                      ⚡ {String(obj.intent || '').replaceAll('_', ' ')}
                                    </div>
                                    {obj.guidance && (
                                      <div style={{ fontSize: '12px', color: '#A3A3A3' }}>{obj.guidance}</div>
                                    )}
                                  </div>
                                ))}
                              </div>
                            </div>
                          </div>
                        )}

                        {/* Pages Crawled */}
                        {(primaryPages.length > 0 || pagesCrawled.length > 0) && (
                          <div className="col-md-6">
                            <div className="ws-section">
                              <div className="ws-section-title">Pages Crawled</div>
                              <div className="d-flex flex-wrap gap-2">
                                {(primaryPages.length > 0 ? primaryPages : pagesCrawled).slice(0, 10).map((page, i) => {
                                  const pt = (page.page_type || 'general').toLowerCase();
                                  return (
                                    <a
                                      key={i}
                                      href={page.url}
                                      target="_blank"
                                      rel="noreferrer"
                                      className="ws-page-chip text-decoration-none"
                                      title={page.title || page.url}
                                    >
                                      <span className={`page-type-dot ${pt}`}></span>
                                      <span className="text-capitalize">{pt}</span>
                                    </a>
                                  );
                                })}
                              </div>
                              {k.content_inventory?.noise_filtered && (
                                <div className="mt-3">
                                  <span style={{ fontSize: '11px', color: '#4ADE80', fontWeight: 500 }}>✓ Noise filtered</span>
                                </div>
                              )}
                            </div>
                          </div>
                        )}

                        {/* Quality Checks */}
                        {quality?.checks?.length > 0 && (
                          <div className="col-12">
                            <div className="ws-section">
                              <div className="ws-section-title">Readiness Checks</div>
                              <div className="row g-2">
                                {quality.checks.map((check, i) => (
                                  <div key={i} className="col-md-4 col-6">
                                    <div className="d-flex align-items-center gap-2" style={{ fontSize: '12px', color: check.passed ? '#E5E5E5' : '#6B6B6B' }}>
                                      <span style={{ fontSize: '14px' }}>{check.passed ? '✅' : '⬜'}</span>
                                      <span>{check.message}</span>
                                    </div>
                                  </div>
                                ))}
                              </div>
                              {quality.warnings?.length > 0 && (
                                <div className="mt-3 pt-3" style={{ borderTop: '1px solid #262626' }}>
                                  <div style={{ fontSize: '11px', color: '#F87171', textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 600, marginBottom: '8px' }}>⚠ Review warnings</div>
                                  {quality.warnings.slice(0, 4).map((w, i) => (
                                    <div key={i} style={{ fontSize: '12px', color: '#A3A3A3', marginBottom: '4px' }}>• {w}</div>
                                  ))}
                                </div>
                              )}
                            </div>
                          </div>
                        )}

                        {/* Source Evidence */}
                        {sourceEvidenceFromKnowledge(k).length > 0 && (
                          <div className="col-12">
                            <div className="ws-section">
                              <div className="ws-section-title">Source Evidence</div>
                              <div className="d-flex flex-column gap-2">
                                {sourceEvidenceFromKnowledge(k).map((url) => (
                                  <a key={url} href={url} target="_blank" rel="noreferrer" className="text-truncate text-primary" style={{ fontSize: '12px', textDecoration: 'underline' }}>
                                    🔗 {url}
                                  </a>
                                ))}
                              </div>
                            </div>
                          </div>
                        )}
                      </div>

                      {/* ── Conversation Guidance ── */}
                      {guidanceSummary(k).hasItems && (
                        <div className="ws-section mb-4">
                          <div className="ws-section-title">Conversation Guidance</div>
                          <div className="row g-3">
                            {guidanceSummary(k).questions.length > 0 && (
                              <div className="col-md-4">
                                <div style={{ fontSize: '11px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: '#E5E5E5', marginBottom: '8px' }}>📋 Qualification</div>
                                <ul className="mb-0 ps-3" style={{ fontSize: '12px', color: '#A3A3A3', lineHeight: 1.5 }}>
                                  {guidanceSummary(k).questions.map((q, i) => <li key={i} style={{ marginBottom: '4px' }}>{q}</li>)}
                                </ul>
                              </div>
                            )}
                            {guidanceSummary(k).objections.length > 0 && (
                              <div className="col-md-4">
                                <div style={{ fontSize: '11px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: '#E5E5E5', marginBottom: '8px' }}>⚡ Objections</div>
                                <ul className="mb-0 ps-3" style={{ fontSize: '12px', color: '#A3A3A3', lineHeight: 1.5 }}>
                                  {guidanceSummary(k).objections.map((item, i) => (
                                    <li key={i} style={{ marginBottom: '4px' }}>
                                      <span className="text-capitalize text-white">{String(item.intent || 'objection').replaceAll('_', ' ')}</span>
                                      {item.guidance ? `: ${item.guidance}` : ''}
                                    </li>
                                  ))}
                                </ul>
                              </div>
                            )}
                            {guidanceSummary(k).faqs.length > 0 && (
                              <div className="col-md-4">
                                <div style={{ fontSize: '11px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: '#E5E5E5', marginBottom: '8px' }}>❓ FAQs</div>
                                <ul className="mb-0 ps-3" style={{ fontSize: '12px', color: '#A3A3A3', lineHeight: 1.5 }}>
                                  {guidanceSummary(k).faqs.map((item, i) => <li key={i} style={{ marginBottom: '4px' }}>{item.question}</li>)}
                                </ul>
                              </div>
                            )}
                          </div>
                        </div>
                      )}

                      {/* ── Actions ── */}
                      <div className="d-flex justify-content-between align-items-center flex-wrap gap-3 p-3 rounded-3" style={{ background: '#1E1E1E', border: '1px solid #3D3D3D' }}>
                        <div style={{ fontSize: '12px', color: '#A3A3A3' }}>
                          💡 Applying saves a Flow V2 draft only. Live calls and published runtime stay unchanged.
                        </div>
                        <div className="d-flex gap-2">
                          <button
                            type="button"
                            className="btn btn-outline-light btn-sm"
                            style={{ padding: '6px 14px' }}
                            onClick={() => handlePreflightGeneratedDraft()}
                            disabled={scrapePreflightLoading || scrapeApplyLoading || scrapeLoading || !FLOW_VISUALIZATION_ENABLED}
                          >
                            {scrapePreflightLoading ? '⏳ Checking…' : '🔍 Preflight'}
                          </button>
                          <button
                            type="button"
                            className="btn btn-sm px-4 fw-semibold"
                            style={{ background: '#FFFFFF', color: '#0A0A0A', border: 'none' }}
                            onClick={() => handleApplyGeneratedDraft()}
                            disabled={scrapeApplyLoading || scrapePreflightLoading || scrapeLoading || !FLOW_VISUALIZATION_ENABLED}
                          >
                            {scrapeApplyLoading ? '💾 Saving…' : '💾 Save to Flow Draft'}
                          </button>
                        </div>
                      </div>
                      {!FLOW_VISUALIZATION_ENABLED && (
                        <div style={{ fontSize: '11px', color: '#6B6B6B', marginTop: '8px' }}>Enable flow visualization to review this draft in the flow editor.</div>
                      )}
                    </>
                  );
                })()}

                {/* ── Job status strip (when no draft yet) ── */}
                {!scrapeDraft && (scrapeJob || scrapeStatus) && (
                  <div className="d-flex gap-3 mb-4">
                    {scrapeJob && (
                      <div className="flex-fill rounded p-3" style={{ background: '#1E1E1E', border: '1px solid #3D3D3D' }}>
                        <div style={{ fontSize: '11px', color: '#6B6B6B', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '4px' }}>Job Status</div>
                        <div style={{ fontSize: '14px', fontWeight: 500, color: '#FFFFFF', textTransform: 'capitalize' }}>{scrapeJob.status || 'queued'}</div>
                      </div>
                    )}
                    {scrapeStatus && (
                      <div className="flex-fill rounded p-3" style={{ background: '#1E1E1E', border: '1px solid #3D3D3D' }}>
                        <div style={{ fontSize: '11px', color: '#6B6B6B', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '4px' }}>Step</div>
                        <div style={{ fontSize: '14px', fontWeight: 500, color: '#FFFFFF' }}>{scrapeStatus}</div>
                      </div>
                    )}
                  </div>
                )}

                {/* ── Previous Drafts ── */}
                <div className="mt-4 p-4 rounded-3" style={{ background: '#141414', border: '1px solid #262626' }}>
                  <div className="d-flex justify-content-between align-items-center mb-4">
                    <div>
                      <div style={{ fontSize: '14px', fontWeight: 600, color: '#FFFFFF', marginBottom: '2px' }}>🕒 Previous Drafts</div>
                      <div style={{ fontSize: '12px', color: '#6B6B6B' }}>Generated website drafts for this agent</div>
                    </div>
                    <button
                      type="button"
                      className="btn btn-outline-secondary btn-sm"
                      onClick={() => loadScrapeDraftHistory(scrapeAgent)}
                      disabled={scrapeHistoryLoading || scrapeLoading}
                    >
                      {scrapeHistoryLoading ? 'Loading…' : '↻ Refresh'}
                    </button>
                  </div>
                  {scrapeHistoryLoading ? (
                    <div style={{ fontSize: '12px', color: '#6B6B6B' }}>Loading drafts…</div>
                  ) : scrapeDraftHistory.length === 0 ? (
                    <div style={{ fontSize: '12px', color: '#6B6B6B' }}>No previous generated drafts for this agent.</div>
                  ) : (
                    <div className="d-flex flex-column gap-3">
                      {scrapeDraftHistory.map((draft) => (
                        <div key={draft.id} className="ws-history-item d-flex justify-content-between align-items-center gap-3" style={{ background: '#1E1E1E' }}>
                          <div>
                            <div style={{ fontSize: '13px', fontWeight: 500, color: '#FFFFFF', marginBottom: '2px' }}>
                              🏢 {draft.knowledge?.company?.name || draft.knowledge?.domain || 'Website Draft'}
                            </div>
                            <div style={{ fontSize: '11px', color: '#A3A3A3' }}>
                              <span className="text-capitalize">{String(draft.knowledge?.industry || 'unknown').replaceAll('_', ' ')}</span>
                              {' · '}
                              <span style={{ fontFamily: 'monospace' }}>{(draft.id || '').substring(0, 8)}</span>
                              {' · '}
                              {draft.status || 'draft'}
                            </div>
                            {draftQuality(draft) && (
                              <span style={{ display: 'inline-block', padding: '2px 8px', border: '1px solid #3D3D3D', borderRadius: '4px', fontSize: '10px', color: '#E5E5E5', textTransform: 'capitalize', marginTop: '6px' }}>
                                {draftQuality(draft).level} readiness · {draftQuality(draft).score}/100
                              </span>
                            )}
                            {draft.reviewed_at && (
                              <div style={{ fontSize: '11px', color: '#4ADE80', marginTop: '6px' }}>
                                ✅ Saved to Flow Draft · {new Date(draft.reviewed_at).toLocaleString()}
                              </div>
                            )}
                          </div>
                          <div className="d-flex gap-2 flex-shrink-0">
                            <button
                              type="button"
                              className="btn btn-outline-light btn-sm"
                              onClick={() => setSummaryModalDraft(draft)}
                            >
                              View Summary
                            </button>
                            <button
                              type="button"
                              className="btn btn-outline-light btn-sm"
                              onClick={() => handlePreflightGeneratedDraft(draft)}
                              disabled={scrapePreflightLoading || scrapeApplyLoading || scrapeLoading || !FLOW_VISUALIZATION_ENABLED}
                            >
                              {scrapePreflightLoading ? '⏳' : '🔍 Preflight'}
                            </button>
                            <button
                              type="button"
                              className="btn btn-sm"
                              style={{ background: '#FFFFFF', color: '#0A0A0A', border: 'none', padding: '4px 12px' }}
                              onClick={() => handleApplyGeneratedDraft(draft)}
                              disabled={scrapeApplyLoading || scrapePreflightLoading || scrapeLoading || !FLOW_VISUALIZATION_ENABLED}
                            >
                              Apply
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              {/* ── Footer ── */}
              <div className="modal-footer" style={{ background: '#141414', borderTop: '1px solid #262626', padding: '16px 24px' }}>
                <button type="button" className="btn btn-outline-secondary" onClick={closeScrapeModal} disabled={scrapeLoading}>Close</button>
              </div>
            </div>
          </div>
          {summaryModalDraft && (
            <GenerateSummaryModal 
              agent={scrapeAgent} 
              draft={summaryModalDraft} 
              onClose={() => setSummaryModalDraft(null)} 
            />
          )}
        </div>
      )}

      {/* Modal for Creating or Editing Agent */}
      {showModal && (
        <div className="modal show d-block" tabIndex="-1" style={{ backgroundColor: 'rgba(0,0,0,0.8)', backdropFilter: 'blur(4px)' }}>
          <div className="modal-dialog modal-lg modal-dialog-centered">
            <div className="modal-content" style={{ background: '#141414', border: '1px solid #262626', borderRadius: '16px' }}>
              <div className="modal-header" style={{ borderBottom: '1px solid #262626', padding: '24px 32px 16px' }}>
                <h5 className="modal-title fw-bold" style={{ color: '#FFFFFF' }}>{modalMode === 'edit' ? 'Edit Voice Agent' : 'Create New Voice Agent'}</h5>
                <button type="button" className="btn-close btn-close-white shadow-none" onClick={closeModal}></button>
              </div>
              <div className="modal-body" style={{ padding: '24px 32px' }}>
                <form onSubmit={handleSubmit}>
                  {/* General Settings */}
                  <h6 style={{ fontSize: '12px', fontWeight: 600, color: '#A3A3A3', textTransform: 'uppercase', letterSpacing: '0.06em', borderBottom: '1px solid #262626', paddingBottom: '8px', marginBottom: '16px' }}>1. General Settings</h6>
                  <div className="row g-3 mb-4">
                    <div className="col-md-6">
                      <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, color: '#E5E5E5', marginBottom: '6px' }}>Agent Type (Persona)</label>
                      <select style={{ width: '100%', background: '#1E1E1E', border: '1px solid #3D3D3D', borderRadius: '8px', padding: '10px 12px', color: '#FFFFFF', fontSize: '13px', outline: 'none' }} value={formData.agent_type} onChange={e => handleAgentTypeChange(e.target.value)}>
                        {Object.entries(AGENT_TYPE_TEMPLATES).map(([value, config]) => (
                          <option key={value} value={value}>{config.label}</option>
                        ))}
                      </select>
                      <div style={{ fontSize: '11px', color: '#6B6B6B', marginTop: '6px' }}>Sets the default prompt and data fields.</div>
                    </div>
                    <div className="col-md-6">
                      <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, color: '#E5E5E5', marginBottom: '6px' }}>Assign to User Email</label>
                      <input type="email" style={{ width: '100%', background: '#1E1E1E', border: '1px solid #3D3D3D', borderRadius: '8px', padding: '10px 12px', color: '#FFFFFF', fontSize: '13px', outline: 'none' }} required value={formData.assigned_email} onChange={e => setFormData({...formData, assigned_email: e.target.value})} placeholder="client@example.com" />
                      <div style={{ fontSize: '11px', color: '#6B6B6B', marginTop: '6px' }}>Isolates agent to this client account.</div>
                    </div>
                  </div>

                  {/* Voice & Speech */}
                  <h6 style={{ fontSize: '12px', fontWeight: 600, color: '#A3A3A3', textTransform: 'uppercase', letterSpacing: '0.06em', borderBottom: '1px solid #262626', paddingBottom: '8px', marginBottom: '16px' }}>2. Voice & Speech Configuration</h6>
                  <div className="row g-3 mb-3">
                    {/* Voice Persona — only shown when NOT using Smallest AI (Smallest AI uses the panel below) */}
                    {formData.tts_provider !== 'smallest' && formData.tts_provider !== 'magpie' &&(
                      <div className="col-md-4">
                        <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, color: '#E5E5E5', marginBottom: '6px' }}>Voice Persona</label>
                        <select
                          style={{ width: '100%', background: '#1E1E1E', border: '1px solid #3D3D3D', borderRadius: '8px', padding: '10px 12px', color: '#FFFFFF', fontSize: '13px', outline: 'none' }}
                          value={formData.voice}
                          onChange={e => {
                            const newVoice = e.target.value;
                            // Auto-select the correct TTS engine when a Sarvam voice is picked
                            const autoTts = SARVAM_VOICE_NAMES.has(newVoice.toLowerCase())
                              ? 'sarvam'
                              : formData.tts_provider;
                            setFormData({ ...formData, voice: newVoice, tts_provider: autoTts });
                          }}
                        >
                          <option value="shreya">Shreya — Professional Female (Sarvam AI)</option>
                          <option value="ishita">Ishita — Dynamic Female (Sarvam AI)</option>
                          <option value="shubh">Shubh — Friendly Male (Sarvam AI)</option>
                        </select>
                        <div style={{ fontSize: '11px', color: '#6B6B6B', marginTop: '5px' }}>Selecting a Sarvam voice auto-sets TTS engine below.</div>
                      </div>
                    )}
                    <div className="col-md-4">
                      <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, color: '#E5E5E5', marginBottom: '6px' }}>Language</label>
                      <input type="text" style={{ width: '100%', background: '#1E1E1E', border: '1px solid #3D3D3D', borderRadius: '8px', padding: '10px 12px', color: '#FFFFFF', fontSize: '13px', outline: 'none' }} value={formData.language} onChange={e => setFormData({...formData, language: e.target.value})} placeholder="e.g. English, Hindi" />
                    </div>
                    <div className="col-md-4">
                      <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, color: '#E5E5E5', marginBottom: '6px' }}>Max Duration (sec)</label>
                      <input type="number" style={{ width: '100%', background: '#1E1E1E', border: '1px solid #3D3D3D', borderRadius: '8px', padding: '10px 12px', color: '#FFFFFF', fontSize: '13px', outline: 'none' }} value={formData.max_duration} onChange={e => setFormData({...formData, max_duration: parseInt(e.target.value) || 300})} />
                    </div>
                  </div>

                  <div className="row g-3 mb-4">
                    <div className="col-md-4">
                      <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, color: '#E5E5E5', marginBottom: '6px' }}>Telephony Provider</label>
                      <select style={{ width: '100%', background: '#1E1E1E', border: '1px solid #3D3D3D', borderRadius: '8px', padding: '10px 12px', color: '#FFFFFF', fontSize: '13px', outline: 'none' }} value={formData.provider} onChange={e => setFormData({...formData, provider: e.target.value})}>
                        <option value="twilio">{getProviderLabel('telephony', 'twilio')}</option>
                        <option value="demo">{getProviderLabel('telephony', 'demo')}</option>
                      </select>
                    </div>
                    <div className="col-md-4">
                      <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, color: '#E5E5E5', marginBottom: '6px' }}>STT Engine</label>
                      <select style={{ width: '100%', background: '#1E1E1E', border: '1px solid #3D3D3D', borderRadius: '8px', padding: '10px 12px', color: '#FFFFFF', fontSize: '13px', outline: 'none' }} value={formData.stt_provider} onChange={e => setFormData({...formData, stt_provider: e.target.value})}>
                        <option value="groq">{getProviderLabel('stt', 'groq')}</option>
                        <option value="deepgram">{getProviderLabel('stt', 'deepgram')}</option>
                        <option value="smallest">{getProviderLabel('stt', 'smallest')}</option>
                      </select>
                    </div>
                    <div className="col-md-4">
                      <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, color: '#E5E5E5', marginBottom: '6px' }}>TTS Engine</label>
                      <select
                        style={{ width: '100%', background: '#1E1E1E', border: '1px solid #3D3D3D', borderRadius: '8px', padding: '10px 12px', color: '#FFFFFF', fontSize: '13px', outline: 'none' }}
                        value={formData.tts_provider}
                        onChange={e => setFormData({...formData, tts_provider: e.target.value})}
                      >
                        <option value="sarvam">{getProviderLabel('tts', 'sarvam')}</option>
                        <option value="smallest">{getProviderLabel('tts', 'smallest')}</option>
                        <option value="edge">{getProviderLabel('tts', 'edge')}</option>
                        <option value="cartesia">{getProviderLabel('tts', 'cartesia')}</option>
                        <option value="indic_parler">{getProviderLabel('tts', 'indic_parler')}</option>

                        <option value="magpie">{getProviderLabel('tts', 'magpie')}</option>

                      </select>
                      {/* Live badge showing the active model + voice */}
                      <div style={{
                        marginTop: '8px', padding: '5px 10px',
                        background: formData.tts_provider === 'sarvam' ? 'rgba(99,102,241,0.12)'
                          : formData.tts_provider === 'smallest' ? 'rgba(127,86,217,0.12)'
                          : formData.tts_provider === 'cartesia' ? 'rgba(20,184,166,0.12)'

                          :formData.tts_provider === 'magpie' ? 'rgba(118,185,0,0.12)'

                          : 'rgba(255,255,255,0.05)',
                        border: `1px solid ${
                          formData.tts_provider === 'sarvam' ? 'rgba(99,102,241,0.35)'
                          : formData.tts_provider === 'smallest' ? 'rgba(127,86,217,0.35)'
                          : formData.tts_provider === 'cartesia' ? 'rgba(20,184,166,0.35)'

                          : formData.tts_provider === 'magpie' ? 'rgba(118,185,0,0.35)'

                          : '#3D3D3D'}`,
                        borderRadius: '6px', fontSize: '11px',
                        color: formData.tts_provider === 'sarvam' ? '#A5B4FC'
                          : formData.tts_provider === 'smallest' ? '#C4B5FD'
                          : formData.tts_provider === 'cartesia' ? '#5EEAD4'

                          :formData.tts_provider === 'magpie' ? '#76B900'

                          : '#A3A3A3',
                        display: 'flex', alignItems: 'center', gap: '5px'
                      }}>
                        <span style={{ fontWeight: 700 }}>Active:</span>
                        <span>{
                          formData.tts_provider === 'sarvam'
                            ? `Sarvam AI · ${(formData.voice || 'shreya').charAt(0).toUpperCase() + (formData.voice || 'shreya').slice(1)}`
                            : formData.tts_provider === 'smallest'
                            ? `Smallest AI · ${formData.smallest_voice || 'anika'}`
                            : formData.tts_provider === 'cartesia'
                            ? 'Cartesia Sonic'
                            : formData.tts_provider === 'indic_parler'
                            ? 'Indic Parler (Local)'

                            /* : formData.tts_provider === 'magpie'
                            ? 'NVIDIA Magpie Multilingual TTS' */
                            : formData.tts_provider === 'magpie'
                            ? `NVIDIA Magpie · ${formData.magpie_speaker || 'Sofia'} 
                            (${(formData.language || '').toLowerCase().includes('hindi') ? 'Hindi 🇮🇳' : 'English 🇬🇧'})`

                            : 'Microsoft Edge Neural'
                        }</span>
                      </div>
                    </div>
                  </div>

                  {formData.tts_provider === 'cartesia' && (
                    <div style={{ background: '#1E1E1E', border: '1px solid #3D3D3D', borderRadius: '8px', padding: '16px', marginBottom: '24px' }}>
                      <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, color: '#FFFFFF', marginBottom: '6px' }}>Cartesia Premium Voice</label>
                      <select style={{ width: '100%', background: '#141414', border: '1px solid #3D3D3D', borderRadius: '6px', padding: '8px 10px', color: '#FFFFFF', fontSize: '13px', outline: 'none' }} value={formData.cartesia_voice_id} onChange={e => setFormData({...formData, cartesia_voice_id: e.target.value})}>
                        {CARTESIA_FEMALE_VOICES.map((voice) => (
                          <option key={voice.value} value={voice.value}>{voice.label}</option>
                        ))}
                      </select>
                      <div style={{ fontSize: '11px', color: '#A3A3A3', marginTop: '6px' }}>Recommended for native Indian-style English, Hindi, Hinglish, and Marathi.</div>
                    </div>
                  )}

                  {/* {formData.tts_provider === 'smallest' && (
                    <div style={{ background: '#1A1A2E', border: '1px solid #3D3D5C', borderRadius: '10px', padding: '16px', marginBottom: '24px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '14px', flexWrap: 'wrap', gap: '8px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <span style={{ fontSize: '13px', fontWeight: 600, color: '#FFFFFF' }}>Smallest AI — Select Voice</span>
                          <span style={{ fontSize: '10px', background: '#7F56D9', color: '#fff', borderRadius: '4px', padding: '2px 8px', fontWeight: 700 }}>⚡ ~100ms Latency</span>
                        </div>

                        {/* Gender Filter Pills 
                        <div style={{ display: 'flex', gap: '4px', background: '#0D0D1A', padding: '3px', borderRadius: '6px', border: '1px solid #2A2A44' }}>
                          <button
                            type="button"
                            onClick={() => setSmallestGenderFilter('all')}
                            style={{ padding: '3px 10px', fontSize: '11px', fontWeight: 600, borderRadius: '4px', border: 'none', background: smallestGenderFilter === 'all' ? '#5B4FBE' : 'transparent', color: smallestGenderFilter === 'all' ? '#FFF' : '#A3A3A3', cursor: 'pointer' }}
                          >
                            All Voices
                          </button>
                          <button
                            type="button"
                            onClick={() => setSmallestGenderFilter('female')}
                            style={{ padding: '3px 10px', fontSize: '11px', fontWeight: 600, borderRadius: '4px', border: 'none', background: smallestGenderFilter === 'female' ? '#5B4FBE' : 'transparent', color: smallestGenderFilter === 'female' ? '#FFF' : '#A3A3A3', cursor: 'pointer' }}
                          >
                            ♀ Female
                          </button>
                          <button
                            type="button"
                            onClick={() => setSmallestGenderFilter('male')}
                            style={{ padding: '3px 10px', fontSize: '11px', fontWeight: 600, borderRadius: '4px', border: 'none', background: smallestGenderFilter === 'male' ? '#2A7FBF' : 'transparent', color: smallestGenderFilter === 'male' ? '#FFF' : '#A3A3A3', cursor: 'pointer' }}
                          >
                            ♂ Male
                          </button>
                        </div>
                      </div>

                      {/* Single Unified Voice Select Dropdown *
                      <div>
                        <label style={{ display: 'block', fontSize: '11px', fontWeight: 600, color: '#A3A3A3', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                          Voice Persona
                        </label>
                        <select
                          style={{ width: '100%', background: '#0D0D1A', border: '1px solid #5B4FBE', borderRadius: '6px', padding: '9px 12px', color: '#FFFFFF', fontSize: '13px', outline: 'none' }}
                          value={formData.smallest_voice || DEFAULT_SMALLEST_VOICE}
                          onChange={e => e.target.value && setFormData({...formData, smallest_voice: e.target.value, voice: e.target.value})}
                        >
                          {(smallestGenderFilter === 'all' || smallestGenderFilter === 'female') && (
                            <optgroup label="♀ FEMALE VOICES">
                              {SMALLEST_FEMALE_VOICES.map(v => (
                                <option key={v.value} value={v.value}>{v.label}</option>
                              ))}
                            </optgroup>
                          )}
                          {(smallestGenderFilter === 'all' || smallestGenderFilter === 'male') && (
                            <optgroup label="♂ MALE VOICES">
                              {SMALLEST_MALE_VOICES.map(v => (
                                <option key={v.value} value={v.value}>{v.label}</option>
                              ))}
                            </optgroup>
                          )}
                        </select>
                      </div>

                      <div style={{ marginTop: '10px', fontSize: '12px', color: '#8B8BA7', display: 'flex', alignItems: 'center', gap: '6px' }}>
                        Selected voice: <strong style={{ color: '#C4B5FD', textTransform: 'capitalize' }}>{formData.smallest_voice || DEFAULT_SMALLEST_VOICE}</strong>
                        <span style={{ fontSize: '10px', background: SMALLEST_FEMALE_VOICES.some(v => v.value === (formData.smallest_voice || DEFAULT_SMALLEST_VOICE)) ? 'rgba(127, 86, 217, 0.2)' : 'rgba(42, 127, 191, 0.2)', color: SMALLEST_FEMALE_VOICES.some(v => v.value === (formData.smallest_voice || DEFAULT_SMALLEST_VOICE)) ? '#C4B5FD' : '#93C5FD', padding: '1px 6px', borderRadius: '4px', border: `1px solid ${SMALLEST_FEMALE_VOICES.some(v => v.value === (formData.smallest_voice || DEFAULT_SMALLEST_VOICE)) ? '#5B4FBE' : '#2A7FBF'}` }}>
                          {SMALLEST_FEMALE_VOICES.some(v => v.value === (formData.smallest_voice || DEFAULT_SMALLEST_VOICE)) ? '♀ Female' : '♂ Male'}
                        </span>
                        · Supports English, Hindi, Hinglish + 30 Indian languages
                      </div>
                    </div>
              )}
 */}

                                  {formData.tts_provider === 'smallest' && (
                    <div style={{ background: '#1A1A2E', border: '1px solid #3D3D5C', borderRadius: '10px', padding: '16px', marginBottom: '24px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '14px', flexWrap: 'wrap', gap: '8px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <span style={{ fontSize: '13px', fontWeight: 600, color: '#FFFFFF' }}>Smallest AI — Select Voice</span>
                          <span style={{ fontSize: '10px', background: '#7F56D9', color: '#fff', borderRadius: '4px', padding: '2px 8px', fontWeight: 700 }}>⚡ ~100ms Latency</span>
                        </div>
                        {/* Gender Filter Pills */}
                        <div style={{ display: 'flex', gap: '4px', background: '#0D0D1A', padding: '3px', borderRadius: '6px', border: '1px solid #2A2A44' }}>
                          <button
                            type="button"
                            onClick={() => setSmallestGenderFilter('all')}
                            style={{ padding: '3px 10px', fontSize: '11px', fontWeight: 600, borderRadius: '4px', border: 'none', background: smallestGenderFilter === 'all' ? '#5B4FBE' : 'transparent', color: smallestGenderFilter === 'all' ? '#FFF' : '#A3A3A3', cursor: 'pointer' }}
                          >
                            All Voices
                          </button>
                          <button
                            type="button"
                            onClick={() => setSmallestGenderFilter('female')}
                            style={{ padding: '3px 10px', fontSize: '11px', fontWeight: 600, borderRadius: '4px', border: 'none', background: smallestGenderFilter === 'female' ? '#5B4FBE' : 'transparent', color: smallestGenderFilter === 'female' ? '#FFF' : '#A3A3A3', cursor: 'pointer' }}
                          >
                            ♀ Female
                          </button>
                          <button
                            type="button"
                            onClick={() => setSmallestGenderFilter('male')}
                            style={{ padding: '3px 10px', fontSize: '11px', fontWeight: 600, borderRadius: '4px', border: 'none', background: smallestGenderFilter === 'male' ? '#2A7FBF' : 'transparent', color: smallestGenderFilter === 'male' ? '#FFF' : '#A3A3A3', cursor: 'pointer' }}
                          >
                            ♂ Male
                          </button>
                        </div>
                      </div>

                      {/* Single Unified Voice Select Dropdown */}
                      <div>
                        <label style={{ display: 'block', fontSize: '11px', fontWeight: 600, color: '#A3A3A3', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                          Voice Persona
                        </label>
                        <select
                          style={{ width: '100%', background: '#0D0D1A', border: '1px solid #5B4FBE', borderRadius: '6px', padding: '9px 12px', color: '#FFFFFF', fontSize: '13px', outline: 'none' }}
                          value={formData.smallest_voice || DEFAULT_SMALLEST_VOICE}
                          onChange={e => e.target.value && setFormData({...formData, smallest_voice: e.target.value, voice: e.target.value})}
                        >
                          {(smallestGenderFilter === 'all' || smallestGenderFilter === 'female') && (
                            <optgroup label="♀ FEMALE VOICES">
                              {SMALLEST_FEMALE_VOICES.map(v => (
                                <option key={v.value} value={v.value}>{v.label}</option>
                              ))}
                            </optgroup>
                          )}
                          {(smallestGenderFilter === 'all' || smallestGenderFilter === 'male') && (
                            <optgroup label="♂ MALE VOICES">
                              {SMALLEST_MALE_VOICES.map(v => (
                                <option key={v.value} value={v.value}>{v.label}</option>
                              ))}
                            </optgroup>
                          )}
                        </select>
                      </div>

                      <div style={{ marginTop: '10px', fontSize: '12px', color: '#8B8BA7', display: 'flex', alignItems: 'center', gap: '6px' }}>
                        Selected voice: <strong style={{ color: '#C4B5FD', textTransform: 'capitalize' }}>{formData.smallest_voice || DEFAULT_SMALLEST_VOICE}</strong>
                        <span style={{ fontSize: '10px', background: SMALLEST_FEMALE_VOICES.some(v => v.value === (formData.smallest_voice || DEFAULT_SMALLEST_VOICE)) ? 'rgba(127, 86, 217, 0.2)' : 'rgba(42, 127, 191, 0.2)', color: SMALLEST_FEMALE_VOICES.some(v => v.value === (formData.smallest_voice || DEFAULT_SMALLEST_VOICE)) ? '#C4B5FD' : '#93C5FD', padding: '1px 6px', borderRadius: '4px', border: `1px solid ${SMALLEST_FEMALE_VOICES.some(v => v.value === (formData.smallest_voice || DEFAULT_SMALLEST_VOICE)) ? '#5B4FBE' : '#2A7FBF'}` }}>
                          {SMALLEST_FEMALE_VOICES.some(v => v.value === (formData.smallest_voice || DEFAULT_SMALLEST_VOICE)) ? '♀ Female' : '♂ Male'}
                        </span>
                        · Supports English, Hindi, Hinglish + 30 Indian languages
                      </div>
                    </div>
                  )}

                  {/* NVIDIA Magpie Voice & Language Selection Panel */}
                  {formData.tts_provider === 'magpie' && (
                    <div style={{ background: '#121A0F', border: '1px solid rgba(118,185,0,0.35)', borderRadius: '10px', padding: '16px', marginBottom: '24px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '14px', flexWrap: 'wrap', gap: '8px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <span style={{ fontSize: '13px', fontWeight: 600, color: '#FFFFFF' }}>NVIDIA Magpie Multilingual</span>
                          <span style={{ fontSize: '10px', background: '#76B900', color: '#0A0A0A', borderRadius: '4px', padding: '2px 8px', fontWeight: 700 }}>🇮🇳 Hindi & English</span>
                        </div>
                      </div>

                      <div className="row g-3">
                        {/* Speaker Voice Dropdown */}
                        <div className="col-md-7">
                          <label style={{ display: 'block', fontSize: '11px', fontWeight: 600, color: '#A3A3A3', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                            Speaker Voice
                          </label>
                          <select
                            style={{ width: '100%', background: '#0D140B', border: '1px solid #76B900', borderRadius: '6px', padding: '9px 12px', color: '#FFFFFF', fontSize: '13px', outline: 'none' }}
                            value={formData.magpie_speaker || DEFAULT_MAGPIE_VOICE}
                            onChange={e => {
                              const spk = e.target.value;
                              const isHindi = (formData.language || '').toLowerCase().includes('hindi');
                              const loc = isHindi ? 'HI-IN' : 'EN-US';
                              setFormData({
                                ...formData,
                                magpie_speaker: spk,
                                voice: `Magpie-Multilingual.${loc}.${spk}`
                              });
                            }}
                          >
                            <optgroup label="♀ FEMALE VOICES">
                              {MAGPIE_FEMALE_VOICES.map(v => (
                                <option key={v.value} value={v.value}>{v.label}</option>
                              ))}
                            </optgroup>
                            <optgroup label="♂ MALE VOICES">
                              {MAGPIE_MALE_VOICES.map(v => (
                                <option key={v.value} value={v.value}>{v.label}</option>
                              ))}
                            </optgroup>
                          </select>
                        </div>

                        {/* Language Selector (English / Hindi) */}
                        <div className="col-md-5">
                          <label style={{ display: 'block', fontSize: '11px', fontWeight: 600, color: '#A3A3A3', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                            Language
                          </label>
                          <select
                            style={{ width: '100%', background: '#0D140B', border: '1px solid #3D3D3D', borderRadius: '6px', padding: '9px 12px', color: '#FFFFFF', fontSize: '13px', outline: 'none' }}
                            value={(formData.language || '').toLowerCase().includes('hindi') ? 'Hindi' : 'English'}
                            onChange={e => {
                              const newLang = e.target.value;
                              const loc = newLang === 'Hindi' ? 'HI-IN' : 'EN-US';
                              const spk = formData.magpie_speaker || DEFAULT_MAGPIE_VOICE;
                              setFormData({
                                ...formData,
                                language: newLang,
                                voice: `Magpie-Multilingual.${loc}.${spk}`
                              });
                            }}
                          >
                            <option value="English">English 🇬🇧 (en-US)</option>
                            <option value="Hindi">Hindi 🇮🇳 (hi-IN)</option>
                          </select>
                        </div>
                      </div>

                      <div style={{ marginTop: '12px', fontSize: '12px', color: '#A3A3A3', display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span>Active Voice Identifier:</span>
                        <code style={{ background: '#0D140B', color: '#76B900', padding: '2px 8px', borderRadius: '4px', border: '1px solid rgba(118,185,0,0.3)', fontSize: '11px' }}>
                          Magpie-Multilingual.{(formData.language || '').toLowerCase().includes('hindi') ? 'HI-IN' : 'EN-US'}.{formData.magpie_speaker || DEFAULT_MAGPIE_VOICE}
                        </code>
                      </div>
                    </div>
                  )}
                  {/* Behavior & Integration */}
                  <h6 style={{ fontSize: '12px', fontWeight: 600, color: '#A3A3A3', textTransform: 'uppercase', letterSpacing: '0.06em', borderBottom: '1px solid #262626', paddingBottom: '8px', marginBottom: '16px' }}>3. Behavior & Integration</h6>
                  
                  <div className="mb-3">
                    <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, color: '#E5E5E5', marginBottom: '6px' }}>Data Extraction Fields</label>
                    <input type="text" style={{ width: '100%', background: '#1E1E1E', border: '1px solid #3D3D3D', borderRadius: '8px', padding: '10px 12px', color: '#FFFFFF', fontSize: '13px', outline: 'none' }} value={formData.data_fields} onChange={e => setFormData({...formData, data_fields: e.target.value})} placeholder="interested, budget, location" />
                    <div style={{ fontSize: '11px', color: '#6B6B6B', marginTop: '6px' }}>Comma separated list of variables to extract during the call.</div>
                  </div>

                  <div className="mb-3">
                    <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, color: '#E5E5E5', marginBottom: '6px' }}>Agent Prompt / Script (Editable)</label>
                    <textarea style={{ width: '100%', background: '#1E1E1E', border: '1px solid #3D3D3D', borderRadius: '8px', padding: '12px', color: '#FFFFFF', fontSize: '13px', fontFamily: 'monospace', outline: 'none', resize: 'vertical' }} rows="8" required value={formData.script} onChange={e => setFormData({...formData, script: e.target.value})} placeholder="You are an AI assistant..."></textarea>
                  </div>

                  <div className="mb-4">
                    <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, color: '#E5E5E5', marginBottom: '6px' }}>Import Custom JSON Schema (Optional)</label>
                    <input type="file" style={{ width: '100%', background: '#1E1E1E', border: '1px solid #3D3D3D', borderRadius: '8px', padding: '8px 12px', color: '#FFFFFF', fontSize: '13px' }} accept=".json" onChange={handleFileUpload} />
                    <div style={{ fontSize: '11px', color: '#6B6B6B', marginTop: '6px' }}>Upload a JSON file to override the default flow structure.</div>
                    {formData.custom_json && (
                      <div style={{ background: 'rgba(74,222,128,0.08)', color: '#4ADE80', padding: '8px 12px', borderRadius: '6px', fontSize: '12px', marginTop: '8px' }}>
                        ✓ Custom JSON loaded and ready to save.
                      </div>
                    )}
                  </div>

                  <div className="d-flex justify-content-end gap-3 pt-4 mt-2" style={{ borderTop: '1px solid #262626' }}>
                    <button type="button" className="btn btn-outline-secondary" onClick={closeModal}>Cancel</button>
                    <button type="submit" style={{ background: '#FFFFFF', color: '#0A0A0A', border: 'none', borderRadius: '6px', padding: '8px 20px', fontWeight: 600, fontSize: '14px' }}>{modalMode === 'edit' ? 'Save Changes' : 'Create Agent'}</button>
                  </div>
                </form>
              </div>
            </div>
          </div>
        </div>
      )}
    </DashboardLayout>
  );
}
