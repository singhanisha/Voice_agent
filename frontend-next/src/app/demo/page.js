'use client';
import { useAuth, clientProfile } from '@/context/AuthContext';
import DashboardLayout from '@/components/DashboardLayout';
import { useVoiceSocket } from '@/hooks/useVoiceSocket';
import { useSearchParams } from 'next/navigation';
import { Suspense, useMemo, useState, useEffect } from 'react';
import { 
  Home, Briefcase, Stethoscope, TrendingUp, Shield, GraduationCap, 
  Mic, ClipboardList, Play, User, Bot, PlaySquare, Square,
  MessageSquare, Activity
} from 'lucide-react';
import { getProviderLabel } from '@/lib/providerDisplay';

const DOMAIN_METADATA = {
  'real_estate_sales': { agent: 'Neha', name: 'Real Estate',  icon: <Home      size={36} strokeWidth={1.5} /> },
  'recruitment':       { agent: 'Sarah', name: 'Recruitment', icon: <Briefcase size={36} strokeWidth={1.5} /> },
  'healthcare':        { agent: 'Maya',  name: 'Healthcare',  icon: <Stethoscope size={36} strokeWidth={1.5} /> },
  'finance':           { agent: 'Arjun', name: 'Finance',     icon: <TrendingUp size={36} strokeWidth={1.5} /> },
  'insurance':         { agent: 'Ananya',name: 'Insurance',   icon: <Shield     size={36} strokeWidth={1.5} /> },
  'education':         { agent: 'Priya', name: 'Education',   icon: <GraduationCap size={36} strokeWidth={1.5} /> },
};

// Short display names for TTS providers in the dropdown
const TTS_SHORT_LABELS = {
  sarvam:      'Sarvam AI',
  smallest:    'Smallest AI',
  edge:        'Edge TTS',
  cartesia:    'Cartesia',
  indic_parler:'Indic Parler',
};

// Short voice persona labels (map raw DB value → readable name)
const VOICE_SHORT_LABELS = {
  shreya:   'Shreya',
  ishita:   'Ishita',
  shubh:    'Shubh',
  priya:    'Priya',
  neha:     'Neha',
  aditya:   'Aditya',
  ashutosh: 'Ashutosh',
  anika:    'Anika',
  devansh:  'Devansh',
};

/**
 * Build a rich option label for an agent dropdown entry.
 * Example: "Real Estate Sales – Default Voice  ·  Sarvam AI · Shreya  (real estate sales)"
 */
function getAgentOptionLabel(agent) {
  const name = agent.name || agent.agent_name || 'Unnamed Agent';
  const agentType = agent.agent_type ? agent.agent_type.replace(/_/g, ' ') : 'voice agent';
  const ttsProvider = agent.tts_provider || 'edge';
  const voiceRaw = (agent.voice || '').toLowerCase();

  const ttsLabel = TTS_SHORT_LABELS[ttsProvider] || ttsProvider;
  const voiceLabel = VOICE_SHORT_LABELS[voiceRaw] || (agent.voice ? agent.voice : '');

  // Build the voice+model badge part
  const modelBadge = voiceLabel
    ? `${ttsLabel} · ${voiceLabel}`
    : ttsLabel;

  return `${name}  ·  ${modelBadge}  (${agentType})`;
}

function DemoCampaignContent() {
  const { activeClient, currentRole, user } = useAuth();
  const searchParams = useSearchParams();
  const queryAgentId = searchParams ? searchParams.get('agentId') : null;

  const [agents, setAgents] = useState([]);
  const [selectedAgentId, setSelectedAgentId] = useState(queryAgentId || '');
  const [loadingAgents, setLoadingAgents] = useState(true);

  const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

  useEffect(() => {
    const fetchAgents = async () => {
      try {
        const res = await fetch(`${API}/api/agents`);
        if (res.ok) {
          const data = await res.json();
          setAgents(Array.isArray(data) ? data : []);
          if (!queryAgentId && data && data.length > 0) {
            setSelectedAgentId(data[0].id || data[0].agent_id);
          }
        }
      } catch (err) {
        console.error('Failed to fetch agents in demo', err);
      } finally {
        setLoadingAgents(false);
      }
    };
    fetchAgents();
  }, [queryAgentId, API]);

  const profile = useMemo(() => {
    return currentRole === 'client' && user?.agentId
      ? { name: user.clientName || user.name, agent: user.agentName || 'Assigned Agent', agentId: user.agentId }
      : clientProfile[activeClient] || { name: user?.name || 'Demo User', agent: 'Neha', agentId: 'real_estate_sales' };
  }, [currentRole, user, activeClient]);

  const resolvedAgentId = selectedAgentId || queryAgentId || profile?.agentId || user?.agentId || 'default';

  const activeAgent = useMemo(() => {
    return agents.find(a => (a.id === resolvedAgentId || a.agent_id === resolvedAgentId));
  }, [agents, resolvedAgentId]);

  const domainMeta = DOMAIN_METADATA[resolvedAgentId] || DOMAIN_METADATA[queryAgentId] || (activeAgent?.agent_type && DOMAIN_METADATA[activeAgent.agent_type]);
  const agentName  = activeAgent?.name || activeAgent?.agent_name || (domainMeta ? domainMeta.agent : (profile?.agent || user?.agentName || 'Assigned Agent'));
  const agentIcon  = domainMeta ? domainMeta.icon : (activeAgent?.agent_type && DOMAIN_METADATA[activeAgent.agent_type] ? DOMAIN_METADATA[activeAgent.agent_type].icon : <Mic size={36} strokeWidth={1.5} />);
  const domainName = activeAgent?.agent_type
    ? (activeAgent.agent_type.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()))
    : (domainMeta ? domainMeta.name : 'Voice Agent');

  const { connect, disconnect, isConnected, statusText, transcripts, events, clearTranscripts } = useVoiceSocket(resolvedAgentId, activeClient);

  const toggleCall = () => {
    if (isConnected) disconnect();
    else connect(true, profile?.name || user?.name || 'Demo User');
  };

  const latestEvent = events.length > 0 ? events[events.length - 1] : null;

  const renderResultCard = (result) => {
    if (!result) return null;
    const skipKeys = ['transcription', 'duration', 'interested', 'campaign_id', 'lead_id', 'lead_name', 'id', 'created_at', 'client_id', 'provider'];
    const dynamicFields = Object.keys(result).filter(
      key => !skipKeys.includes(key) && result[key] !== null && result[key] !== undefined && result[key] !== ''
    );
    const formatKeyLabel = key => key.replace(/_/g, ' ').split(' ').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');

    return (
      <div style={{ background: '#141414', border: '1px solid #262626', borderRadius: '12px', marginTop: '16px', overflow: 'hidden' }}>
        {/* Header */}
        <div style={{ background: '#0A0A0A', borderBottom: '1px solid #262626', padding: '12px 20px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', fontWeight: 500, color: '#FFFFFF' }}>
            <ClipboardList size={14} strokeWidth={1.5} /> Lead Summary ({domainName})
          </span>
          <span style={{ fontSize: '11px', color: '#6B6B6B' }}>Extracted from conversation</span>
        </div>
        <div style={{ padding: '16px 20px' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
            <thead>
              <tr>
                <th style={{ textAlign: 'left', padding: '8px 12px', color: '#6B6B6B', fontWeight: 500, fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.07em', borderBottom: '1px solid #262626', background: '#0A0A0A' }}>Field</th>
                <th style={{ textAlign: 'left', padding: '8px 12px', color: '#6B6B6B', fontWeight: 500, fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.07em', borderBottom: '1px solid #262626', background: '#0A0A0A' }}>Extracted Value</th>
              </tr>
            </thead>
            <tbody>
              <tr style={{ borderBottom: '1px solid #262626' }}>
                <td style={{ padding: '10px 12px', color: '#A3A3A3', fontWeight: 500 }}>Interested</td>
                <td style={{ padding: '10px 12px', fontWeight: 600 }}>
                  {result.interested === 'Yes' || result.interested === true || String(result.interested).toLowerCase() === 'yes' ? (
                    <span style={{ color: '#4ADE80' }}>✓ Yes</span>
                  ) : (
                    <span style={{ color: '#F87171' }}>{String(result.interested || 'No')}</span>
                  )}
                </td>
              </tr>
              {dynamicFields.map(key => (
                <tr key={key} style={{ borderBottom: '1px solid #262626' }}>
                  <td style={{ padding: '10px 12px', color: '#A3A3A3', fontWeight: 500 }}>{formatKeyLabel(key)}</td>
                  <td style={{ padding: '10px 12px', color: '#FFFFFF' }}>{String(result[key])}</td>
                </tr>
              ))}
              <tr>
                <td style={{ padding: '10px 12px', color: '#6B6B6B', background: '#0A0A0A' }}>Conversation stats</td>
                <td style={{ padding: '10px 12px', color: '#6B6B6B', background: '#0A0A0A' }}>{(result.transcription || []).length} turns · {result.duration || '—'}</td>
              </tr>
            </tbody>
          </table>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '12px', paddingTop: '12px', borderTop: '1px solid #262626' }}>
            <span className="cc-live-dot" />
            <span style={{ fontSize: '12px', color: '#4ADE80' }}>Result saved to Call Results tab</span>
          </div>
        </div>
      </div>
    );
  };

  return (
    <DashboardLayout>
      {/* ── Page Header ── */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '32px', flexWrap: 'wrap', gap: '16px' }}>
        <div>
          <h2 style={{ fontSize: '20px', fontWeight: 600, color: '#FFFFFF', marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <PlaySquare size={20} strokeWidth={1.5} /> Demo Campaign
            {domainName && <span style={{ color: '#6B6B6B', fontWeight: 400, fontSize: '16px' }}>({domainName})</span>}
          </h2>
          <p style={{ fontSize: '13px', color: '#A3A3A3', margin: 0 }}>
            Speak to <strong style={{ color: '#FFFFFF' }}>{agentName}</strong> — your voice drives the conversation, dashboard updates live.
          </p>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          {agents.length > 0 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <label style={{ fontSize: '12px', fontWeight: 500, color: '#6B6B6B', textTransform: 'uppercase', letterSpacing: '0.06em', whiteSpace: 'nowrap' }}>
                Agent:
              </label>
              <select
                style={{
                  background: '#141414',
                  border: '1px solid #262626',
                  color: '#FFFFFF',
                  fontSize: '13px',
                  borderRadius: '6px',
                  padding: '6px 10px',
                  outline: 'none',
                  cursor: isConnected ? 'not-allowed' : 'pointer',
                  opacity: isConnected ? 0.5 : 1,
                  minWidth: '340px',
                  maxWidth: '520px'
                }}
                value={resolvedAgentId}
                onChange={e => setSelectedAgentId(e.target.value)}
                disabled={isConnected}
              >
                {agents.map(agent => (
                  <option key={agent.id || agent.agent_id} value={agent.id || agent.agent_id}>
                    {getAgentOptionLabel(agent)}
                  </option>
                ))}
              </select>
            </div>
          )}
          {/* Live status dot */}
          {isConnected ? <span className="cc-live-dot" title="Connected" /> : <span className="cc-dead-dot" title="Not connected" />}
        </div>
      </div>

      {/* ── Main grid ── */}
      <div style={{ display: 'grid', gridTemplateColumns: '5fr 7fr', gap: '24px' }}>
        
        {/* ── Left: Agent panel ── */}
        <div>
          <div style={{
            background: 'linear-gradient(145deg, #1A1A1A 0%, #0F0F0F 100%)',
            border: '1px solid #2A2A2A',
            borderRadius: '16px',
            boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
            overflow: 'hidden',
            display: 'flex',
            flexDirection: 'column',
            transition: 'all 0.3s ease'
          }}>
            <div style={{ padding: '48px 32px', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
              {/* Agent avatar — bordered ring, not filled disc */}
              <div style={{
                width: '110px', height: '110px',
                borderRadius: '50%',
                border: isConnected ? '2px solid #FFFFFF' : '1px solid #333333',
                background: 'linear-gradient(135deg, #262626 0%, #141414 100%)',
                boxShadow: isConnected ? '0 0 20px rgba(255,255,255,0.1)' : 'inset 0 4px 10px rgba(0,0,0,0.5)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                marginBottom: '24px',
                color: isConnected ? '#FFFFFF' : '#888888',
                transition: 'all 0.4s cubic-bezier(0.4, 0, 0.2, 1)',
                transform: isConnected ? 'scale(1.05)' : 'scale(1)'
              }}>
                {agentIcon}
              </div>
              
              <div style={{ fontSize: '24px', fontWeight: 600, color: '#FFFFFF', marginBottom: '8px', letterSpacing: '-0.02em' }}>
                {agentName}
              </div>
              
              <div style={{ 
                fontSize: '12px', 
                color: isConnected ? '#FFFFFF' : '#6B6B6B', 
                textTransform: 'uppercase', 
                letterSpacing: '0.1em', 
                marginBottom: '40px',
                display: 'flex', alignItems: 'center', gap: '6px'
              }}>
                {isConnected && <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: '#FFFFFF', animation: 'pulse 1.5s infinite' }} />}
                {statusText || 'Idle'}
              </div>

              {/* Call button */}
              <button
                onClick={toggleCall}
                style={{
                  width: '100%',
                  padding: '16px',
                  fontSize: '14px',
                  fontWeight: 600,
                  borderRadius: '8px',
                  border: isConnected ? '1px solid #444444' : '1px solid #FFFFFF',
                  background: isConnected ? 'transparent' : '#FFFFFF',
                  color: isConnected ? '#FFFFFF' : '#000000',
                  cursor: 'pointer',
                  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '10px',
                  transition: 'all 0.2s ease',
                  letterSpacing: '0.02em',
                  boxShadow: isConnected ? 'none' : '0 4px 12px rgba(255,255,255,0.15)'
                }}
                onMouseEnter={e => {
                  if (isConnected) { 
                    e.currentTarget.style.background = '#1A1A1A'; 
                    e.currentTarget.style.borderColor = '#666666';
                  } else { 
                    e.currentTarget.style.transform = 'translateY(-2px)';
                    e.currentTarget.style.boxShadow = '0 6px 16px rgba(255,255,255,0.2)';
                  }
                }}
                onMouseLeave={e => {
                  if (isConnected) { 
                    e.currentTarget.style.background = 'transparent'; 
                    e.currentTarget.style.borderColor = '#444444';
                  } else { 
                    e.currentTarget.style.transform = 'translateY(0)';
                    e.currentTarget.style.boxShadow = '0 4px 12px rgba(255,255,255,0.15)';
                  }
                }}
              >
                {isConnected
                  ? <><Square size={16} strokeWidth={2} /> End Conversation</>
                  : <><Play size={16} fill="#000000" strokeWidth={0} /> Start Demo Call</>
                }
              </button>
              <div style={{ fontSize: '11px', color: '#6B6B6B', marginTop: '16px', display: 'flex', alignItems: 'center', gap: '4px' }}>
                <Mic size={12} /> Allow mic access when prompted
              </div>
            </div>
          </div>
        </div>

        {/* ── Right: Live Feed + Transcript ── */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          
          {/* Live Feed panel */}
          <div style={{ background: 'linear-gradient(180deg, #141414 0%, #0A0A0A 100%)', border: '1px solid #262626', borderRadius: '12px', overflow: 'hidden', boxShadow: '0 4px 20px rgba(0,0,0,0.2)' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '16px 20px', borderBottom: '1px solid #262626', background: '#111111' }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', fontWeight: 600, color: '#E5E5E5', letterSpacing: '0.02em' }}>
                <Activity size={16} strokeWidth={2} /> Live Feed
              </span>
              <span style={{ fontSize: '11px', color: '#888888', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 500 }}>
                {latestEvent ? (latestEvent.type === 'call_completed' ? 'Completed' : 'Active') : 'Waiting'}
              </span>
            </div>

            <div style={{ padding: '20px', minHeight: '100px' }}>
              {!latestEvent && (
                <div className="cc-empty-state" style={{ color: '#888888' }}>
                  <Activity size={24} strokeWidth={1} style={{ marginBottom: '8px', opacity: 0.5 }} />
                  <span style={{ fontSize: '13px' }}>Press Start Demo Call to see live updates</span>
                </div>
              )}
              {latestEvent && (
                <div style={{
                  padding: '12px 16px',
                  background: '#1E1E1E',
                  border: `1px solid ${latestEvent.type === 'call_completed' ? '#4ADE80' : '#3D3D3D'}`,
                  borderRadius: '8px'
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                    <span style={{ fontWeight: 600, fontSize: '13px', color: '#FFFFFF', display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <User size={14} strokeWidth={2} /> {latestEvent.leadName || profile?.name || user?.name || 'Demo User'}
                    </span>
                    <span style={{
                      padding: '4px 10px',
                      borderRadius: '4px',
                      fontSize: '10px',
                      fontWeight: 600,
                      textTransform: 'uppercase',
                      letterSpacing: '0.08em',
                      border: `1px solid ${latestEvent.type === 'call_completed' ? '#FFFFFF' : '#444444'}`,
                      color: latestEvent.type === 'call_completed' ? '#000000' : '#E5E5E5',
                      background: latestEvent.type === 'call_completed' ? '#FFFFFF' : '#1A1A1A'
                    }}>
                      {latestEvent.type.replace('call_', '')}
                    </span>
                  </div>
                  {latestEvent.snippet && (
                    <div style={{ fontSize: '13px', color: '#CCCCCC', fontStyle: 'italic', lineHeight: 1.6 }}>"{latestEvent.snippet}"</div>
                  )}
                  {latestEvent.type === 'call_completed' && (
                    <div style={{ fontSize: '12px', color: '#4ADE80', marginTop: '8px' }}>
                      ✓ {latestEvent.transcripts?.length || 0} turns — result saved to Call Results
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Live Transcript panel */}
          <div style={{ background: 'linear-gradient(180deg, #141414 0%, #0A0A0A 100%)', border: '1px solid #262626', borderRadius: '12px', overflow: 'hidden', flex: 1, boxShadow: '0 4px 20px rgba(0,0,0,0.2)' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '16px 20px', borderBottom: '1px solid #262626', background: '#111111' }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', fontWeight: 600, color: '#E5E5E5', letterSpacing: '0.02em' }}>
                <MessageSquare size={16} strokeWidth={2} /> Live Transcript
              </span>
              <button
                onClick={clearTranscripts}
                style={{
                  background: 'none', border: '1px solid #333333', cursor: 'pointer',
                  fontSize: '10px', color: '#A3A3A3', fontWeight: 600,
                  textTransform: 'uppercase', letterSpacing: '0.08em',
                  padding: '4px 10px', borderRadius: '4px',
                  transition: 'all 0.2s ease'
                }}
                onMouseEnter={e => { e.currentTarget.style.color = '#000000'; e.currentTarget.style.background = '#FFFFFF'; }}
                onMouseLeave={e => { e.currentTarget.style.color = '#A3A3A3'; e.currentTarget.style.background = 'transparent'; }}
              >
                Clear
              </button>
            </div>
            <div style={{ padding: '20px', overflowY: 'auto', minHeight: '240px', maxHeight: '380px' }}>
              {transcripts.length === 0 && (
                <div className="cc-empty-state" style={{ color: '#888888', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: '12px' }}>
                  <MessageSquare size={24} strokeWidth={1} style={{ opacity: 0.5 }} />
                  <span style={{ fontSize: '13px' }}>Transcript will appear as you speak</span>
                </div>
              )}
              {transcripts.map((msg, i) => {
                const isAgent = msg.speaker === 'agent';
                return (
                  <div key={i} style={{
                    padding: '14px 16px',
                    background: isAgent ? 'linear-gradient(90deg, #1A1A1A 0%, #141414 100%)' : '#000000',
                    border: '1px solid #2A2A2A',
                    borderLeft: `3px solid ${isAgent ? '#FFFFFF' : '#555555'}`,
                    borderRadius: '8px',
                    marginBottom: '12px',
                    boxShadow: '0 2px 8px rgba(0,0,0,0.2)'
                  }}>
                    <div style={{ fontSize: '10px', color: '#888888', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '6px', fontWeight: 600 }}>
                      {isAgent ? <><Bot size={13} strokeWidth={2} color="#FFFFFF" /> <span style={{color: '#E5E5E5'}}>{agentName}</span></> : <><User size={13} strokeWidth={2} /> You</>}
                    </div>
                    <div style={{ fontSize: '14px', color: isAgent ? '#FFFFFF' : '#CCCCCC', lineHeight: 1.6, fontWeight: 400 }}>{msg.text}</div>
                  </div>
                );
              })}
            </div>
          </div>

          {latestEvent?.type === 'call_completed' && renderResultCard(latestEvent.result)}
        </div>
      </div>
    </DashboardLayout>
  );
}

export default function DemoCampaign() {
  return (
    <Suspense fallback={
      <DashboardLayout>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '50vh' }}>
          <div className="cc-spinner" />
        </div>
      </DashboardLayout>
    }>
      <DemoCampaignContent />
    </Suspense>
  );
}
