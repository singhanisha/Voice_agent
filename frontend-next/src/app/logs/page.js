'use client';
import { useState, useEffect } from 'react';
import DashboardLayout from '@/components/DashboardLayout';

export default function LogsPage() {
  const [campaigns, setCampaigns] = useState([]);
  const [selectedCampaign, setSelectedCampaign] = useState('');
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);

  // Transcripts lazy loading state
  const [expandedRow, setExpandedRow] = useState(null);
  const [transcriptsCache, setTranscriptsCache] = useState({});
  const [loadingTranscript, setLoadingTranscript] = useState(false);

  const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

  useEffect(() => {
    fetch(`${API}/api/campaigns`)
      .then(r => r.ok ? r.json() : [])
      .then(data => {
        const campaignsArray = Array.isArray(data) ? data : [];
        setCampaigns(campaignsArray);
        if (campaignsArray.length > 0) {
          setSelectedCampaign(campaignsArray[0].id);
        }
        setLoading(false);
      })
      .catch(e => {
        console.error(e);
        setLoading(false);
      });
  }, [API]);

  useEffect(() => {
    if (!selectedCampaign) return;
    let cancelled = false;
    Promise.resolve()
      .then(() => {
        if (!cancelled) setLoading(true);
        return fetch(`${API}/api/campaigns/${selectedCampaign}/results`);
      })
      .then(r => r.ok ? r.json() : [])
      .then(data => {
        if (cancelled) return;
        setLogs(Array.isArray(data) ? data : []);
        setLoading(false);
      })
      .catch(e => {
        if (cancelled) return;
        console.error(e);
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [selectedCampaign, API]);

  const toggleTranscript = async (leadId) => {
    if (expandedRow === leadId) {
      setExpandedRow(null);
      return;
    }
    
    setExpandedRow(leadId);
    
    if (!transcriptsCache[leadId]) {
      setLoadingTranscript(true);
      try {
        const res = await fetch(`${API}/api/results/${leadId}/transcript`);
        const data = await res.json();
        setTranscriptsCache(prev => ({ ...prev, [leadId]: data }));
      } catch (e) {
        console.error("Failed to load transcript", e);
      } finally {
        setLoadingTranscript(false);
      }
    }
  };

  return (
    <DashboardLayout>
      <div className="d-flex justify-content-between align-items-center mb-4">
        <div>
          <h2 className="h4 fw-bold mb-1">Call Logs & QA</h2>
          <p className="text-muted small mb-0">Review transcripts and audio recordings</p>
        </div>
        <button className="btn btn-outline-secondary btn-sm px-3 shadow-sm" disabled={logs.length === 0}>
          Export Logs
        </button>
      </div>
      
      <div className="card border-0 shadow-sm mb-4">
        <div className="card-body py-3 d-flex align-items-center gap-3">
          <label className="fw-bold small text-muted text-nowrap mb-0">Select Campaign:</label>
          <select 
            className="form-select form-select-sm w-auto" 
            value={selectedCampaign} 
            onChange={(e) => setSelectedCampaign(e.target.value)}
          >
            {campaigns.length === 0 && <option value="">No campaigns available</option>}
            {campaigns.map(c => (
              <option key={c.id} value={c.id}>{c.id}</option>
            ))}
          </select>
        </div>
      </div>

      {loading ? (
        <div className="text-center py-5"><span className="spinner-border text-primary"></span></div>
      ) : logs.length === 0 ? (
        <div className="card border-0 shadow-sm">
          <div className="card-body p-5 text-center text-muted">
            <div className="fs-1 mb-3">📞</div>
            <h6>No Call Logs Found</h6>
            <p className="small">Logs for the selected campaign will appear here once calls are completed.</p>
          </div>
        </div>
      ) : (
        <div className="card border-0 shadow-sm">
          <div className="table-responsive">
            <table className="table table-hover align-middle mb-0 text-nowrap">
              <thead className="table-light text-muted small">
                <tr>
                  <th className="py-3 px-4">Lead Name</th>
                  <th className="py-3">Phone</th>
                  <th className="py-3">Outcome</th>
                  <th className="py-3">Interested</th>
                  <th className="py-3">Duration</th>
                  <th className="py-3 text-end px-4">Recording & QA</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((log, i) => (
                  <tr key={log.id || log.lead_id}>
                    <td className="px-4 py-3 fw-medium">{log.name || log.lead_name || 'Unknown'}</td>
                    <td className="py-3">{log.phone || '—'}</td>
                    <td className="py-3">
                      <span className={`badge ${log.status === 'Connected' ? 'bg-success-subtle text-success border border-success-subtle' : 'bg-light text-secondary border'}`}>
                        {log.outcome || log.status || 'unknown'}
                      </span>
                    </td>
                    <td className="py-3">
                      {log.interested === 'Yes' ? (
                        <span className="text-success fw-bold">✓ Yes</span>
                      ) : log.interested === 'No' ? (
                        <span className="text-danger">✗ No</span>
                      ) : (
                        <span className="text-muted">—</span>
                      )}
                    </td>
                    <td className="py-3 text-muted small">{log.duration || '—'}</td>
                    <td className="py-3 px-4 text-end">
                      <div className="d-flex align-items-center justify-content-end gap-2">
                        {log.has_recording ? (
                          <audio 
                            src={`${API}${log.recording_url}`} 
                            controls 
                            preload="metadata" 
                            style={{ height: '32px', width: '200px' }} 
                          />
                        ) : null}
                        
                        <button 
                          className={`btn btn-sm ${expandedRow === (log.lead_id || log.id) ? 'btn-secondary' : 'btn-outline-secondary'}`}
                          onClick={() => toggleTranscript(log.lead_id || log.id)}
                          disabled={!log.has_transcript}
                        >
                          📄 {expandedRow === (log.lead_id || log.id) ? 'Hide' : 'Transcript'}
                        </button>
                      </div>
                    </td>
                  </tr>
                )).reduce((acc, tr, i) => {
                  const log = logs[i];
                  const leadId = log.lead_id || log.id;
                  acc.push(tr);
                  
                  if (expandedRow === leadId) {
                    const transcript = transcriptsCache[leadId] || [];
                    acc.push(
                      <tr key={`exp-${leadId}`} className="bg-light">
                        <td colSpan="6" className="p-0 border-bottom-0">
                          <div className="p-4 border-start border-4 border-secondary m-3 bg-white rounded shadow-sm text-start">
                            <h6 className="fw-bold mb-3 d-flex align-items-center gap-2">
                              <span>💬 Conversation Transcript</span>
                              {loadingTranscript && !transcriptsCache[leadId] && (
                                <div className="spinner-border spinner-border-sm text-secondary" role="status"></div>
                              )}
                            </h6>
                            
                            <div className="transcript-chat" style={{ maxHeight: '300px', overflowY: 'auto' }}>
                              {transcript.length === 0 && !loadingTranscript ? (
                                <div className="text-muted small italic">No conversation data available.</div>
                              ) : (
                                <div className="d-flex flex-column gap-2 pe-2">
                                  {transcript.map((msg, idx) => (
                                    <div key={idx} className={`d-flex ${msg.speaker === 'user' ? 'justify-content-end' : 'justify-content-start'}`}>
                                      <div 
                                        className={`px-3 py-2 rounded-3 ${msg.speaker === 'user' ? 'bg-primary text-white' : 'bg-light border'}`}
                                        style={{ maxWidth: '75%', fontSize: '0.9rem' }}
                                      >
                                        <div className={`small fw-bold mb-1 ${msg.speaker === 'user' ? 'text-white-50' : 'text-secondary'}`} style={{ fontSize: '0.7rem' }}>
                                          {msg.speaker === 'user' ? (log.name || log.lead_name || 'User') : 'AI Agent'}
                                        </div>
                                        <div>{msg.text || msg.content}</div>
                                      </div>
                                    </div>
                                  ))}
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
      )}
    </DashboardLayout>
  );
}
