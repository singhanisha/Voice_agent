// Shared options for client-side agent creation / editing.
// AGENT_TYPES values MUST match the backend's VALID_AGENT_TYPES (main.py),
// otherwise the backend silently turns the agent into "real_estate_sales".
export const AGENT_TYPES = [
  { value: 'real_estate_sales', label: 'Real Estate Sales' },
  { value: 'finance', label: 'Finance' },
  { value: 'insurance', label: 'Insurance' },
  { value: 'education', label: 'Education' },
  { value: 'hr', label: 'HR / Recruitment' },
];

export const agentTypeLabel = (value) =>
  AGENT_TYPES.find((t) => t.value === value)?.label ||
  String(value || 'General').replaceAll('_', ' ').replace(/\b\w/g, (c) => c.toUpperCase());

export const SMALLEST_FEMALE_VOICES = [
  { label: 'Anika - Sales / Conversational', value: 'anika' },
  { label: 'Divya - Customer Support', value: 'divya' },
  { label: 'Avni - Customer Support', value: 'avni' },
  { label: 'Kavya - Insurance Agent', value: 'kavya' },
  { label: 'Naina - Conversational / Narration', value: 'naina' },
  { label: 'Nikita - Conversational / Narration', value: 'nikita' },
  { label: 'Siya - Customer Support', value: 'siya' },
  { label: 'Aditi - Narration / Educational', value: 'aditi' },
  { label: 'Maya - Conversational / Advertisement', value: 'maya' },
  { label: 'Rishika - Insurance Agent', value: 'rishika' },
  { label: 'Maithili - Customer Support', value: 'maithili' },
  { label: 'Aisha - Sales / Advertisement', value: 'aisha' },
];

export const SMALLEST_MALE_VOICES = [
  { label: 'Devansh - Sales / Advertisement', value: 'devansh' },
  { label: 'Dhruv - Sales / Narration', value: 'dhruv' },
  { label: 'Karan - Insurance Agent', value: 'karan' },
  { label: 'Arjun - Insurance Agent / Educational', value: 'arjun' },
  { label: 'Vivaan - Sales / Narration', value: 'vivaan' },
  { label: 'Atharv - Sales / Conversational', value: 'atharv' },
  { label: 'Veer - Customer Support', value: 'veer' },
  { label: 'Kunal - Narration / Educational', value: 'kunal' },
  { label: 'Wasim - Sales / Advertisement', value: 'wasim' },
  { label: 'Siddharth - Narration / Educational', value: 'siddharth' },
  { label: 'Kaustubh - Insurance Agent', value: 'kaustubh' },
];

export const DEFAULT_SMALLEST_VOICE = 'anika';

export const LANGUAGE_OPTIONS = [
  'English', 'Hindi', 'Hinglish'
];