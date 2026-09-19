const STT_PROVIDER_LABELS = {
  groq: 'Groq Whisper Large v3 (Default)',
  deepgram: 'Deepgram Nova-2 (Enhanced)',
  smallest: 'Smallest AI Pulse Pro (Ultra-Fast STT)',
  default: 'Groq Whisper Large v3 (Default)',
};

const TTS_PROVIDER_LABELS = {
  sarvam: 'Sarvam AI Bulbul V3 (Enterprise Indian TTS)',
  smallest: 'Smallest AI Lightning v3.1 (Ultra-Low Latency)',
  edge: 'Microsoft Edge Neural TTS (Free & Fast)',
  cartesia: 'Cartesia Sonic TTS (High Quality)',
  indic_parler: 'Indic Parler (Local Model)',
 
  magpie: 'NVIDIA Magpie TTS Multilingual',
 
  default: 'Microsoft Edge Neural TTS (Free & Fast)',
};

const TELEPHONY_PROVIDER_LABELS = {
  twilio: 'Twilio Voice (Real Phone Calls)',
  demo: 'Web Browser Simulator (Testing)',
  exotel: 'Exotel Voice (India Network)',
  plivo: 'Plivo Voice',
  default: 'Twilio Voice (Real Phone Calls)',
};

const METRIC_LABELS = {
  stt_latency: 'STT latency',
  tts_latency: 'TTS latency',
};

export function getProviderLabel(kind, value) {
  const slug = String(value || 'default').toLowerCase();
  if (kind === 'stt') return STT_PROVIDER_LABELS[slug] || STT_PROVIDER_LABELS.default;
  if (kind === 'tts') return TTS_PROVIDER_LABELS[slug] || TTS_PROVIDER_LABELS.default;
  return TELEPHONY_PROVIDER_LABELS[slug] || TELEPHONY_PROVIDER_LABELS.default;
}

export function formatProviderMetricKey(key) {
  const [metric, provider] = String(key || '').split(':');
  const metricLabel = METRIC_LABELS[metric] || metric.replace(/_/g, ' ');
  const kind = metric?.startsWith('stt') ? 'stt' : metric?.startsWith('tts') ? 'tts' : 'telephony';
  return provider ? `${metricLabel} / ${getProviderLabel(kind, provider)}` : metricLabel;
}
