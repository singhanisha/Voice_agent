'use client';
import { Suspense, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import DashboardLayout from '@/components/DashboardLayout';
import { useAuth } from '@/context/AuthContext';

export default function CallResultsPage() {
  return (
    <Suspense fallback={(
      <DashboardLayout>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '50vh' }}>
          <div className="cc-spinner" />
        </div>
      </DashboardLayout>
    )}>
      <CallResults />
    </Suspense>
  );
}

function CallResults() {
  const { activeClient, currentRole, user } = useAuth();
  const searchParams = useSearchParams();
  const requestedCampaign = searchParams.get('campaign') || '';
  const [campaigns, setCampaigns] = useState([]);
  const [selectedCampaign, setSelectedCampaign] = useState('');
  const [leads, setLeads] = useState([]);
  const [loading, setLoading] = useState(true);

  // Transcripts lazy loading state
  const [expandedRow, setExpandedRow] = useState(null);
  const [transcriptsCache, setTranscriptsCache] = useState({});
  const [loadingTranscript, setLoadingTranscript] = useState(false);

  const isFinserv = activeClient === 'finserv';
  const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
  const resultClientId = currentRole === 'client' ? (user?.clientId || activeClient) : '';
  const resultScopeQuery = resultClientId ? `?clientId=${encodeURIComponent(resultClientId)}` : '';

  const recordingPlaybackUrl = (recordingUrl) => {
    const params = new URLSearchParams();
    params.set('recordingUrl', recordingUrl);
    if (resultClientId) params.set('clientId', resultClientId);
    return `${API}/api/recordings/protected?${params.toString()}`;
  };

  // Step 1: Fetch all campaigns so user can pick one
  useEffect(() => {
    const campaignParams = resultClientId ? `?clientId=${encodeURIComponent(resultClientId)}` : '';
    fetch(`${API}/api/campaigns${campaignParams}`)
      .then(r => r.ok ? r.json() : [])
      .then(data => {
        const arr = Array.isArray(data) ? data : [];
        setCampaigns(arr);
        if (arr.length > 0) {
          const requested = requestedCampaign && arr.find(c => c.id === requestedCampaign);
          setSelectedCampaign(prev => {
            if (requested) return requested.id;
            return prev && arr.some(c => c.id === prev) ? prev : arr[0].id;
          });
        } else {
          setSelectedCampaign('');
          setLeads([]);
          setLoading(false);
        }
      })
      .catch(console.error);
  }, [API, resultClientId, requestedCampaign]);

  // Step 2: Poll results for the selected campaign every 5s
  useEffect(() => {
    if (!selectedCampaign) return;
    let active = true;

    const fetchResults = async () => {
      try {
        const campaignId = encodeURIComponent(selectedCampaign);
        const res = await fetch(`${API}/api/campaigns/${campaignId}/results${resultScopeQuery}`);
        const json = await res.json();
        if (active) {
          setLeads(Array.isArray(json) ? json : []);
          setLoading(false);
        }
      } catch (e) {
        console.error('Failed to load results', e);
        if (active) setLoading(false);
      }
    };

    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    fetchResults();
    const int = setInterval(fetchResults, 5000);
    return () => { active = false; clearInterval(int); };
  }, [selectedCampaign, API, resultScopeQuery]);

  const toggleTranscript = async (leadId) => {
    if (expandedRow === leadId) { setExpandedRow(null); return; }
    setExpandedRow(leadId);
    if (!transcriptsCache[leadId]) {
      setLoadingTranscript(true);
      try {
        const transcriptId = encodeURIComponent(leadId);
        const res = await fetch(`${API}/api/results/${transcriptId}/transcript${resultScopeQuery}`);
        const data = await res.json();
        setTranscriptsCache(prev => ({ ...prev, [leadId]: data }));
      } catch (e) {
        console.error('Failed to load transcript', e);
      } finally {
        setLoadingTranscript(false);
      }
    }
  };

  // Hot lead: interested=Yes AND has a scheduled callback/visit
  const isHotLead = (l) => {
    const interested = l.interested === 'Yes';
    const cb = (l.callback || '').toLowerCase();
    const hasCallback = cb && cb !== '—' && cb !== '';
    const ld = l.lead_data || {};
    const visitAgreed = ld.site_visit_agreed || ld.visit_confirmed ||
      cb.includes('sunday') || cb.includes('saturday') ||
      cb.includes('monday') || cb.includes('friday');
    return interested && (hasCallback || visitAgreed);
  };

  const processed = leads.filter(l => l.processed);
  const connected = processed.filter(l => l.status === 'Connected');
  const hotLeads = leads.filter(isHotLead);

  return (
    <DashboardLayout>
      {/* Page header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '24px' }}>
        <div>
          <h2 style={{ fontSize: '20px', fontWeight: 600, color: '#FFFFFF', marginBottom: '4px' }}>
            Call Results — {isFinserv ? 'Renewal Drive' : 'Live Campaign'}
          </h2>
          <p style={{ fontSize: '13px', color: '#A3A3A3', margin: 0 }}>Live conversational data securely written to your structured storage.</p>
        </div>
        <button style={{
          padding: '6px 14px',
          background: 'transparent',
          border: '1px solid #3D3D3D',
          color: '#A3A3A3',
          borderRadius: '6px',
          fontSize: '13px',
          cursor: 'pointer',
          display: 'flex', alignItems: 'center', gap: '6px',
          transition: 'color 100ms ease-out, border-color 100ms ease-out'
        }}>
          ↓ Export CSV
        </button>
      </div>

      {/* Campaign Selector */}
      <div style={{ background: '#141414', border: '1px solid #262626', borderRadius: '12px', padding: '12px 20px', marginBottom: '20px', display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
        <label style={{ fontSize: '11px', fontWeight: 500, color: '#6B6B6B', textTransform: 'uppercase', letterSpacing: '0.07em', whiteSpace: 'nowrap' }}>Campaign:</label>
        <select
          style={{
            background: '#1E1E1E',
            border: '1px solid #262626',
            color: '#FFFFFF',
            fontSize: '13px',
            borderRadius: '6px',
            padding: '6px 10px',
            outline: 'none',
            maxWidth: '320px'
          }}
          value={selectedCampaign}
          onChange={(e) => { setSelectedCampaign(e.target.value); setLeads([]); }}
        >
          {campaigns.length === 0 && <option value="">No campaigns available</option>}
          {campaigns.map(c => (
            <option key={c.id} value={c.id}>{c.name || c.id} ({c.status})</option>
          ))}
        </select>
        {hotLeads.length > 0 && (
          <span style={{ padding: '3px 10px', border: '1px solid #4ADE80', borderRadius: '999px', fontSize: '12px', color: '#4ADE80', background: 'transparent' }}>
            🔥 {hotLeads.length} Hot Lead{hotLeads.length > 1 ? 's' : ''}
          </span>
        )}
      </div>

      {/* Stats */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '12px', marginBottom: '20px' }}>
        {[
          { label: 'Total Contacts', value: leads.length, sub: null, hot: false },
          { label: 'Called', value: processed.length, sub: `${leads.length > 0 ? Math.round((processed.length / leads.length) * 100) : 0}% done`, hot: false },
          { label: 'Connected', value: connected.length, sub: `${processed.length > 0 ? Math.round((connected.length / processed.length) * 100) : 0}% connect rate`, hot: false },
          { label: '🔥 Hot Leads', value: hotLeads.length, sub: hotLeads.length > 0 ? 'Agreed to visit / buy' : 'None yet', hot: true },
        ].map((stat, idx) => (
          <div key={idx} style={{ background: '#141414', border: `1px solid ${stat.hot && hotLeads.length > 0 ? '#4ADE80' : '#262626'}`, borderRadius: '12px', padding: '16px 20px' }}>
            <div style={{ fontSize: '11px', color: '#6B6B6B', textTransform: 'uppercase', letterSpacing: '0.07em', fontWeight: 500, marginBottom: '8px' }}>{stat.label}</div>
            <div style={{ fontSize: '28px', fontWeight: 700, color: stat.hot && hotLeads.length > 0 ? '#4ADE80' : '#FFFFFF', lineHeight: 1 }}>{stat.value}</div>
            {stat.sub && <div style={{ fontSize: '12px', color: '#6B6B6B', marginTop: '4px' }}>{stat.sub}</div>}
          </div>
        ))}
      </div>

      {/* Table */}
      <div style={{ background: '#141414', border: '1px solid #262626', borderRadius: '12px', overflow: 'hidden' }}>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', whiteSpace: 'nowrap' }}>
            <thead>
              <tr>
                {['Name', 'Phone', 'Called At', 'Duration', 'Status', isFinserv ? 'Renewal Confirmed' : 'Interested', 'Callback / Visit', 'Recording & QA'].map(h => (
                  <th key={h} style={{ padding: '10px 16px', fontSize: '11px', color: '#6B6B6B', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.07em', background: '#0A0A0A', borderBottom: '1px solid #262626', textAlign: 'left' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading && leads.length === 0 ? (
                <tr>
                  <td colSpan="8" style={{ background: '#141414' }}>
                    <div className="cc-empty-state">
                      <div className="cc-spinner" />
                      <span>Fetching live results...</span>
                    </div>
                  </td>
                </tr>
              ) : leads.length === 0 ? (
                <tr>
                  <td colSpan="8" style={{ background: '#141414' }}>
                    <div className="cc-empty-state">
                      <span style={{ fontSize: '18px' }}>📋</span>
                      <span>No results found for this campaign. Start the campaign first.</span>
                    </div>
                  </td>
                </tr>
              ) : leads.map((l, i) => (
                <tr
                  key={i}
                  style={{
                    background: isHotLead(l) ? '#1E1E1E' : (i % 2 === 0 ? '#141414' : '#0A0A0A'),
                    borderLeft: isHotLead(l) ? '2px solid #4ADE80' : '2px solid transparent',
                    borderBottom: '1px solid #262626'
                  }}
                >
                  <td style={{ padding: '12px 16px', fontWeight: 500, color: '#FFFFFF' }}>
                    {isHotLead(l) && <span style={{ marginRight: '6px' }}>🔥</span>}
                    {l.name}
                  </td>
                  <td style={{ padding: '12px 16px', color: '#A3A3A3', fontSize: '13px' }}>{l.phone}</td>
                  <td style={{ padding: '12px 16px', color: '#6B6B6B', fontSize: '12px' }}>{l.calledAt || '—'}</td>
                  <td style={{ padding: '12px 16px', color: '#A3A3A3', fontSize: '13px' }}>{l.duration || '—'}</td>
                  <td style={{ padding: '12px 16px' }}>
                    <span style={{
                      padding: '3px 8px',
                      borderRadius: '4px',
                      fontSize: '11px',
                      fontWeight: 500,
                      textTransform: 'uppercase',
                      letterSpacing: '0.05em',
                      border: `1px solid ${l.status === 'Connected' ? '#4ADE80' : l.status === 'No Answer' ? '#6B6B6B' : '#3D3D3D'}`,
                      color: l.status === 'Connected' ? '#4ADE80' : l.status === 'No Answer' ? '#A3A3A3' : '#6B6B6B',
                      background: 'transparent'
                    }}>
                      {l.status}
                    </span>
                  </td>
                  <td style={{ padding: '12px 16px', fontWeight: 500 }}>
                    {l.interested === 'Yes' ? (
                      <span style={{ color: '#4ADE80' }}>✓ Yes</span>
                    ) : l.interested === 'No' ? (
                      <span style={{ color: '#F87171' }}>✗ No</span>
                    ) : (
                      <span style={{ color: '#6B6B6B' }}>—</span>
                    )}
                  </td>
                  <td style={{ padding: '12px 16px', color: '#A3A3A3', fontSize: '13px' }}>
                    {l.callback && l.callback !== '—' ? l.callback : '—'}
                  </td>
                  <td style={{ padding: '12px 16px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      {l.has_recording ? (
                        <audio
                          src={recordingPlaybackUrl(l.recording_url)}
                          controls
                          preload="metadata"
                          style={{ height: '28px', width: '160px' }}
                        />
                      ) : (
                        <span style={{ color: '#6B6B6B', fontSize: '12px', fontStyle: 'italic' }}>No Media</span>
                      )}
                      <button
                        style={{
                          padding: '4px 10px',
                          background: 'transparent',
                          border: `1px solid ${expandedRow === (l.lead_id || l.id) ? '#FFFFFF' : '#3D3D3D'}`,
                          color: expandedRow === (l.lead_id || l.id) ? '#FFFFFF' : '#A3A3A3',
                          borderRadius: '4px',
                          fontSize: '11px',
                          cursor: !l.has_transcript ? 'not-allowed' : 'pointer',
                          opacity: !l.has_transcript ? 0.4 : 1,
                          transition: 'border-color 100ms ease-out, color 100ms ease-out'
                        }}
                        onClick={() => toggleTranscript(l.lead_id || l.id)}
                        disabled={!l.has_transcript}
                      >
                        📄 {expandedRow === (l.lead_id || l.id) ? 'Hide' : 'Transcript'}
                      </button>
                    </div>
                  </td>
                </tr>
              )).reduce((acc, tr, i) => {
                const l = leads[i];
                const leadId = l.lead_id || l.id;
                acc.push(tr);
                if (expandedRow === leadId) {
                  const transcript = transcriptsCache[leadId] || [];
                  acc.push(
                    <tr key={`exp-${leadId}`}>
                      <td colSpan="8" style={{ padding: 0, background: '#0A0A0A', borderBottom: '1px solid #262626' }}>
                        <div style={{ margin: '12px 16px', background: '#141414', border: '1px solid #262626', borderLeft: '2px solid #3D3D3D', borderRadius: '8px', padding: '16px' }}>
                          <div style={{ fontSize: '13px', fontWeight: 500, color: '#FFFFFF', marginBottom: '12px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                            💬 Conversation Transcript
                            {loadingTranscript && !transcriptsCache[leadId] && <div className="cc-spinner" style={{ width: '14px', height: '14px' }} />}
                          </div>
                          <div style={{ maxHeight: '280px', overflowY: 'auto' }}>
                            {transcript.length === 0 && !loadingTranscript ? (
                              <div className="cc-empty-state" style={{ minHeight: '60px' }}>
                                <span>No conversation data available.</span>
                              </div>
                            ) : (
                              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                                {transcript.map((msg, idx) => {
                                  const isUser = msg.speaker === 'user' || msg.role === 'user';
                                  return (
                                    <div key={idx} style={{ display: 'flex', justifyContent: isUser ? 'flex-end' : 'flex-start' }}>
                                      <div style={{
                                        maxWidth: '75%',
                                        padding: '8px 12px',
                                        borderRadius: '6px',
                                        background: isUser ? '#1E1E1E' : '#262626',
                                        border: '1px solid #3D3D3D',
                                        fontSize: '13px',
                                        color: '#FFFFFF'
                                      }}>
                                        <div style={{ fontSize: '10px', color: '#6B6B6B', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: '4px' }}>
                                          {isUser ? (l.name || 'User') : 'AI Agent'}
                                        </div>
                                        <div>{msg.text || msg.content}</div>
                                      </div>
                                    </div>
                                  );
                                })}
                              </div>
                            )}
                          </div>
                        </div>
                      </td>
                    </tr>
                  );
                }
                return acc;
              }, [])}
            </tbody>
          </table>
        </div>
      </div>
    </DashboardLayout>
  );
}
