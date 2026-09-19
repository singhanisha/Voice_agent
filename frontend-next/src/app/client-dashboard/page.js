'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import DashboardLayout from '@/components/DashboardLayout';
import { useAuth, clientProfile } from '@/context/AuthContext';
import { getProviderLabel } from '@/lib/providerDisplay';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const CLIENT_WS_SCOPED_EVENTS_ENABLED = process.env.NEXT_PUBLIC_WS_SCOPED_EVENTS_ENABLED === 'true';

function campaignSlug(name) {
  const slug = String(name || '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 48);
  return `${slug || 'campaign'}-${Date.now()}`;
}

function cleanLeads(rows) {
  return rows
    .map((row) => ({
      name: String(row.name || '').trim(),
      phone: String(row.phone || '').trim(),
    }))
    .filter((row) => row.name && row.phone);
}

function uniqueLeads(rows) {
  const seen = new Set();
  const leads = [];
  cleanLeads(rows).forEach((lead) => {
    const key = lead.phone.replace(/\D/g, '') || lead.phone.toLowerCase();
    if (!key || seen.has(key)) return;
    seen.add(key);
    leads.push(lead);
  });
  return leads;
}

function parseCsvLine(line) {
  const cells = [];
  let current = '';
  let quoted = false;

  for (let index = 0; index < line.length; index += 1) {
    const char = line[index];
    const next = line[index + 1];
    if (char === '"' && quoted && next === '"') {
      current += '"';
      index += 1;
    } else if (char === '"') {
      quoted = !quoted;
    } else if (char === ',' && !quoted) {
      cells.push(current.trim());
      current = '';
    } else {
      current += char;
    }
  }
  cells.push(current.trim());
  return cells;
}

function normalizeCsvHeader(value) {
  return String(value || '').trim().toLowerCase().replace(/[\s_-]+/g, '');
}

function csvToLeads(text) {
  const rows = String(text || '')
    .replace(/^\uFEFF/, '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map(parseCsvLine);

  if (!rows.length) return [];

  const headers = rows[0].map(normalizeCsvHeader);
  const nameIndex = headers.findIndex((header) => ['name', 'fullname', 'leadname', 'customername', 'contactname'].includes(header));
  const phoneIndex = headers.findIndex((header) => ['phone', 'phonenumber', 'mobile', 'mobilenumber', 'number', 'contactnumber'].includes(header));
  const hasHeader = phoneIndex >= 0;
  const dataRows = hasHeader ? rows.slice(1) : rows;
  const resolvedNameIndex = hasHeader && nameIndex >= 0 ? nameIndex : 0;
  const resolvedPhoneIndex = hasHeader ? phoneIndex : rows[0].length === 1 ? 0 : 1;

  return uniqueLeads(dataRows.map((row, index) => {
    const phone = String(row[resolvedPhoneIndex] || '').trim();
    const name = String(row[resolvedNameIndex] || '').trim() || `Lead ${index + 1}`;
    return { name, phone };
  }));
}

function dashboardWsUrl(clientId) {
  const base = API.replace(/^https/, 'wss').replace(/^http/, 'ws').replace(/\/$/, '');
  return `${base}/ws/dashboard?clientId=${encodeURIComponent(clientId)}`;
}

function liveEventLabel(eventType) {
  return String(eventType || '')
    .replace(/^call_/, '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase()) || 'Update';
}

export default function ClientDashboard() {
  const { activeClient, currentRole, user, clientAssignment } = useAuth();
  const profile = currentRole === 'client' && user?.clientId
    ? { name: user.clientName || user.name, agent: user.agentName || 'Assigned Agent', agentId: user.agentId }
    : clientProfile[activeClient];
  
  const availableAgents = clientAssignment?.agents || [];
  const [selectedAgentId, setSelectedAgentId] = useState('');

  const activeAgent = useMemo(() => {
    if (selectedAgentId) return availableAgents.find(a => a.id === selectedAgentId) || null;
    return clientAssignment?.assignment?.agent || availableAgents[0] || null;
  }, [selectedAgentId, availableAgents, clientAssignment]);

  const agentId = activeAgent?.id || user?.agentId || profile?.agentId || 'default';
  const agentName = activeAgent?.name || user?.agentName || profile?.agent || 'Assigned Agent';
  const assignedProvider = activeAgent?.provider || 'demo';
  const clientId = user?.clientId || activeClient;

  // Domain Marketplace State
  const [domains, setDomains] = useState([]);
  const [domainsLoading, setDomainsLoading] = useState(true);

  // Carousel State & Navigation
  const carouselRef = useRef(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(true);

  const checkScroll = useCallback(() => {
    if (carouselRef.current) {
      const { scrollLeft, scrollWidth, clientWidth } = carouselRef.current;
      setCanScrollLeft(scrollLeft > 2);
      setCanScrollRight(scrollLeft + clientWidth < scrollWidth - 5);
    }
  }, []);

  useEffect(() => {
    const el = carouselRef.current;
    if (el) {
      el.addEventListener('scroll', checkScroll);
      // Initial check
      checkScroll();
      // Handle resize
      window.addEventListener('resize', checkScroll);
    }
    return () => {
      if (el) el.removeEventListener('scroll', checkScroll);
      window.removeEventListener('resize', checkScroll);
    };
  }, [domains, checkScroll]);

  // Run another check after domains load
  useEffect(() => {
    if (!domainsLoading) {
      const timer = setTimeout(checkScroll, 100);
      return () => clearTimeout(timer);
    }
  }, [domainsLoading, checkScroll]);

  const handleScroll = (direction) => {
    if (carouselRef.current) {
      const width = carouselRef.current.clientWidth;
      const scrollAmount = direction === 'left' ? -width * 0.75 : width * 0.75;
      carouselRef.current.scrollBy({ left: scrollAmount, behavior: 'smooth' });
    }
  };

  // Custom Agent Request State
  const [showRequestModal, setShowRequestModal] = useState(false);
  const [requestSubmitting, setRequestSubmitting] = useState(false);
  const [requestNotice, setRequestNotice] = useState(null);
  const [requestForm, setRequestForm] = useState({
    name: '',
    company: '',
    email: '',
    phone: '',
    industry: '',
    useCase: '',
    monthlyVolume: '< 1,000',
    notes: '',
  });

  // Common Launch / Activity Feed State
  const [showLaunchModal, setShowLaunchModal] = useState(false);
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState(null);
  const [form, setForm] = useState({
    campaignName: '',
    telephonyProvider: '',
  });
  const [leadRows, setLeadRows] = useState([
    { name: '', phone: '' },
    { name: '', phone: '' },
  ]);
  const [csvLeads, setCsvLeads] = useState([]);
  const [csvFileName, setCsvFileName] = useState('');
  const [csvError, setCsvError] = useState('');
  
  // unified activities feed
  const [recentActivities, setRecentActivities] = useState([]);
  const [activityLoading, setActivityLoading] = useState(true);
  const [lastActivityRefresh, setLastActivityRefresh] = useState('');
  
  const [liveEvents, setLiveEvents] = useState([]);
  const [liveSocketStatus, setLiveSocketStatus] = useState('connecting');
  const liveSocketRef = useRef(null);

  const providerOptions = useMemo(() => {
    const slugs = ['demo'];
    if (assignedProvider && !slugs.includes(assignedProvider)) slugs.push(assignedProvider);
    return slugs;
  }, [assignedProvider]);

  const selectedProvider = form.telephonyProvider || assignedProvider || 'demo';
  const clientLiveEventsEnabled = CLIENT_WS_SCOPED_EVENTS_ENABLED && Boolean(clientId);

  // Initialize requestForm email from user profile
  useEffect(() => {
    if (user?.email) {
      setRequestForm((prev) => ({ ...prev, email: user.email }));
    }
    if (user?.name) {
      setRequestForm((prev) => ({ ...prev, name: user.name }));
    }
  }, [user]);

  // Fetch Domain Registry
  useEffect(() => {
    fetch(`${API}/api/registry/domains`)
      .then((res) => (res.ok ? res.json() : []))
      .then((data) => {
        setDomains(data);
        setDomainsLoading(false);
      })
      .catch((err) => {
        console.error('Failed to fetch domain registry', err);
        setDomainsLoading(false);
      });
  }, []);

  // Fetch Recent Unified Activity
  const loadRecentActivity = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setActivityLoading(true);
    try {
      // 1. Fetch campaigns (both outbound and demo calls)
      let fetchedCampaigns = [];
      if (clientId) {
        const res = await fetch(`${API}/api/campaigns?clientId=${encodeURIComponent(clientId)}`);
        if (res.ok) {
          fetchedCampaigns = await res.json();
        }
      }

      // 2. Fetch custom requests (filter by user email if client, list all if admin)
      let fetchedRequests = [];
      const reqRes = await fetch(`${API}/api/demo-requests`);
      if (reqRes.ok) {
        const reqData = await reqRes.json();
        if (Array.isArray(reqData)) {
          if (currentRole === 'admin') {
            fetchedRequests = reqData;
          } else if (user?.email) {
            fetchedRequests = reqData.filter((r) => r.email?.toLowerCase() === user.email.toLowerCase());
          }
        }
      }

      // Format campaign launches and demo calls
      const campaignActivities = fetchedCampaigns.map((c) => ({
        id: c.id,
        type: c.telephony_provider === 'demo' ? 'demo_call' : 'campaign',
        title: c.name || c.id,
        status: c.status || 'Pending',
        detail: `${c.lead_count ?? 0} lead${c.lead_count === 1 ? '' : 's'} · ${c.result_count ?? 0} result${c.result_count === 1 ? '' : 's'}`,
        timestamp: c.created_at || new Date().toISOString(),
        raw: c
      }));

      // Format custom requests
      const requestActivities = fetchedRequests.map((r) => ({
        id: r.id,
        type: 'custom_request',
        title: `Custom Agent: ${r.use_case || r.industry || 'General Workflows'}`,
        status: r.status || 'New',
        detail: `${r.company || 'Personal'} · Submitted by ${r.name}`,
        timestamp: r.created_at || new Date().toISOString(),
        raw: r
      }));

      // Combine and Sort by Timestamp desc
      const combined = [...campaignActivities, ...requestActivities].sort((a, b) => {
        return new Date(b.timestamp) - new Date(a.timestamp);
      });

      setRecentActivities(combined.slice(0, 10));
      setLastActivityRefresh(new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }));
    } catch (error) {
      console.error('Failed to load recent activity feed', error);
    } finally {
      setActivityLoading(false);
    }
  }, [clientId, currentRole, user?.email]);

  const shouldAutoRefreshCampaigns = useMemo(() => (
    recentActivities.some((act) => act.type !== 'custom_request' && ['active', 'pending'].includes(String(act.status || '').toLowerCase()))
  ), [recentActivities]);

  useEffect(() => {
    loadRecentActivity();
  }, [loadRecentActivity]);

  useEffect(() => {
    if (!shouldAutoRefreshCampaigns) return undefined;
    const interval = window.setInterval(() => {
      loadRecentActivity({ silent: true });
    }, 5000);
    return () => window.clearInterval(interval);
  }, [loadRecentActivity, shouldAutoRefreshCampaigns]);

  // Connect Live Dashboard socket
  useEffect(() => {
    if (!clientLiveEventsEnabled) return undefined;
    const socket = new WebSocket(dashboardWsUrl(clientId));
    liveSocketRef.current = socket;

    socket.onopen = () => setLiveSocketStatus('connected');
    socket.onerror = () => setLiveSocketStatus('error');
    socket.onclose = () => setLiveSocketStatus('closed');
    socket.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        if (!(String(message.type || '').startsWith('call_') || message.type === 'campaign_completed')) return;
        const key = [
          message.type,
          message.campaignId,
          message.leadId,
          message.status,
          message.snippet,
        ].join(':');
        setLiveEvents((current) => {
          if (current[0]?.key === key) return current;
          return [
            {
              key,
              type: message.type,
              label: liveEventLabel(message.type),
              campaignId: message.campaignId,
              leadName: message.leadName || 'Lead',
              status: message.status || '',
              snippet: message.snippet || message.message || '',
              provider: message.provider || selectedProvider,
              receivedAt: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
            },
            ...current,
          ].slice(0, 8);
        });
        if (message.type === 'call_completed' || message.type === 'campaign_completed') {
          loadRecentActivity({ silent: true });
        }
      } catch (error) {
        console.error('Client dashboard WS parse error', error);
      }
    };

    return () => {
      if (liveSocketRef.current === socket) liveSocketRef.current = null;
      if ([WebSocket.CONNECTING, WebSocket.OPEN].includes(socket.readyState)) socket.close();
    };
  }, [clientId, clientLiveEventsEnabled, loadRecentActivity, selectedProvider]);

  // Lead handlers
  const updateLead = (index, key, value) => {
    setLeadRows((rows) => rows.map((row, i) => (i === index ? { ...row, [key]: value } : row)));
  };

  const addLeadRow = () => {
    setLeadRows((rows) => [...rows, { name: '', phone: '' }]);
  };

  const removeLeadRow = (index) => {
    setLeadRows((rows) => (rows.length <= 1 ? rows : rows.filter((_, i) => i !== index)));
  };

  const resetLaunchForm = () => {
    setForm({ campaignName: '', telephonyProvider: '' });
    setLeadRows([{ name: '', phone: '' }, { name: '', phone: '' }]);
    setCsvLeads([]);
    setCsvFileName('');
    setCsvError('');
  };

  // CSV handlers
  const handleCsvUpload = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    setCsvError('');
    try {
      const text = await file.text();
      const parsed = csvToLeads(text);
      if (!parsed.length) {
        setCsvLeads([]);
        setCsvFileName('');
        setCsvError('No valid leads found in the CSV.');
        return;
      }
      setCsvLeads(parsed);
      setCsvFileName(file.name);
    } catch (error) {
      setCsvLeads([]);
      setCsvFileName('');
      setCsvError('Could not read this CSV file.');
    }
  };

  const clearCsv = () => {
    setCsvLeads([]);
    setCsvFileName('');
    setCsvError('');
  };

  // Launch Outbound Campaign
  const launchCampaign = async (event) => {
    event.preventDefault();
    const leads = uniqueLeads([...csvLeads, ...leadRows]);
    if (!form.campaignName.trim()) {
      setNotice({ type: 'danger', text: 'Please enter a campaign name.' });
      return;
    }
    if (!agentId) {
      setNotice({ type: 'danger', text: 'No agent is assigned to this account yet.' });
      return;
    }
    if (!leads.length) {
      setNotice({ type: 'danger', text: 'Add at least one lead with name and phone number.' });
      return;
    }

    const campaignId = campaignSlug(form.campaignName);
    const telephonyProvider = selectedProvider;
    setLoading(true);
    setNotice(null);

    try {
      const uploadRes = await fetch(`${API}/api/leads/upload`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          campaignId,
          campaignName: form.campaignName.trim(),
          agentId,
          telephonyProvider,
          clientId,
          leads,
        }),
      });
      const uploadBody = await uploadRes.json().catch(() => ({}));
      if (!uploadRes.ok) throw new Error(uploadBody.detail || 'Lead upload failed');

      const startRes = await fetch(`${API}/api/campaigns/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          campaignId,
          agentId,
          telephonyProvider,
          clientId,
        }),
      });
      const startBody = await startRes.json().catch(() => ({}));
      if (!startRes.ok) throw new Error(startBody.detail || 'Campaign launch failed');

      const acceptedCount = uploadBody.count || leads.length;
      const ignoredCount = (uploadBody.summary?.duplicates || 0) + (uploadBody.summary?.invalid || 0);
      setShowLaunchModal(false);
      resetLaunchForm();
      await loadRecentActivity();
      setNotice({
        type: 'success',
        text: `Campaign "${form.campaignName.trim()}" started with ${acceptedCount} lead${acceptedCount === 1 ? '' : 's'}${ignoredCount ? `, ${ignoredCount} skipped` : ''}.`,
      });
    } catch (error) {
      setNotice({ type: 'danger', text: error.message || 'Campaign launch failed.' });
    } finally {
      setLoading(false);
    }
  };

  // Submit Custom Agent Request
  const handleRequestSubmit = async (e) => {
    e.preventDefault();
    setRequestSubmitting(true);
    setRequestNotice(null);
    try {
      const payload = {
        name: requestForm.name,
        company: requestForm.company,
        email: requestForm.email,
        phone: requestForm.phone,
        industry: requestForm.industry,
        use_case: requestForm.useCase,
        monthly_volume: requestForm.monthlyVolume,
        additional_notes: requestForm.notes,
      };

      const res = await fetch(`${API}/api/demo-requests`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        throw new Error('Failed to submit demo request.');
      }

      setRequestNotice({ type: 'success', text: 'Thank you! Your custom agent request has been submitted successfully.' });
      setRequestForm({
        name: user?.name || '',
        company: '',
        email: user?.email || '',
        phone: '',
        industry: '',
        useCase: '',
        monthlyVolume: '< 1,000',
        notes: '',
      });
      
      // Reload activity feed
      loadRecentActivity({ silent: true });
    } catch (err) {
      setRequestNotice({ type: 'danger', text: err.message || 'Error submitting request.' });
    } finally {
      setRequestSubmitting(false);
    }
  };

  // Onboarding banner logic: new client with no campaigns/activities
  const isNewClient = recentActivities.length === 0 && availableAgents.length === 0;

  return (
    <DashboardLayout>
      {/* HEADER SECTION */}
      <div className="d-flex justify-content-between align-items-center mb-4">
        <div>
          <h2 className="h4 fw-bold mb-1">Marketplace Dashboard</h2>
          <p className="text-muted small mb-0">Welcome back, {profile?.name || user?.name}</p>
        </div>
        {availableAgents.length > 0 && (
          <button
            className="btn btn-primary px-4 fw-semibold shadow-sm rounded-3"
            onClick={() => {
              setNotice(null);
              setShowLaunchModal(true);
            }}
            disabled={loading}
          >
            {loading ? 'Launching...' : 'Launch Campaign'}
          </button>
        )}
      </div>

      {notice && (
        <div className={`alert alert-${notice.type} py-2 small`} role="status">
          {notice.text}
        </div>
      )}

      {/* ONBOARDING BANNER */}
      {isNewClient && (
        <div className="alert border-0 shadow-sm p-4 mb-4 rounded-3 text-white" style={{ background: 'linear-gradient(135deg, #4f46e5, #7c3aed)' }}>
          <h5 className="fw-bold mb-2">🚀 Welcome to Cosmic Chameleon!</h5>
          <p className="mb-0 small">Welcome to Cosmic Chameleon. Choose a domain to explore. Try out our pre-configured demo voice agents or request a completely customized AI Voice Agent built specifically for your business workflows.</p>
        </div>
      )}

      {/* MARKETPLACE GRID SECTION */}
      <div className="row g-4 mb-4">
        <div className="col-md-8">
          <div className="card border-0 shadow-sm h-100">
            <div className="card-header bg-white border-bottom py-3 d-flex justify-content-between align-items-center">
              <h6 className="mb-0 fw-bold">Available AI Voice Agents</h6>
              <div className="d-flex align-items-center gap-3">
                {/* Carousel Scroll controls */}
                {!domainsLoading && domains.length > 0 && (
                  <div className="d-flex align-items-center gap-1">
                    <button
                      type="button"
                      className="btn btn-light btn-sm rounded-circle d-flex align-items-center justify-content-center border"
                      onClick={() => handleScroll('left')}
                      disabled={!canScrollLeft}
                      style={{ width: '28px', height: '28px', opacity: canScrollLeft ? 1 : 0.4 }}
                      title="Scroll Left"
                    >
                      ‹
                    </button>
                    <button
                      type="button"
                      className="btn btn-light btn-sm rounded-circle d-flex align-items-center justify-content-center border"
                      onClick={() => handleScroll('right')}
                      disabled={!canScrollRight}
                      style={{ width: '28px', height: '28px', opacity: canScrollRight ? 1 : 0.4 }}
                      title="Scroll Right"
                    >
                      ›
                    </button>
                  </div>
                )}
                {availableAgents.length > 1 && (
                  <div className="d-flex align-items-center gap-2">
                    <span className="small text-muted">Active:</span>
                    <select 
                      className="form-select form-select-sm shadow-none w-auto"
                      value={agentId}
                      onChange={(e) => setSelectedAgentId(e.target.value)}
                    >
                      {availableAgents.map(agent => (
                        <option key={agent.id} value={agent.id}>
                          {agent.name}
                        </option>
                      ))}
                    </select>
                  </div>
                )}
              </div>
            </div>
            
            <div className="card-body p-4">
              {domainsLoading ? (
                <div className="text-center py-5">
                  <div className="spinner-border spinner-border-sm text-primary me-2" role="status" />
                  <span className="text-muted small">Loading marketplace domains...</span>
                </div>
              ) : (
                <div className="position-relative">
                  <div 
                    ref={carouselRef}
                    className="carousel-track gap-3 py-2 px-1"
                  >
                    {domains.map((domain) => (
                      <div className="carousel-item-custom flex-shrink-0" key={domain.id}>
                        <div className="card h-100 border border-light shadow-sm carousel-card-hover" style={{ borderRadius: '10px' }}>
                          <div className="card-body p-3 d-flex flex-column">
                            <div className="d-flex align-items-center justify-content-between mb-2">
                              <div className="d-flex align-items-center gap-2">
                                <span className="fs-4">{domain.icon}</span>
                                <h6 className="fw-bold mb-0 text-dark">{domain.name}</h6>
                              </div>
                              <span className="badge bg-light text-secondary border small">Demo: {domain.demo_agent_name}</span>
                            </div>
                            <p className="text-muted small flex-grow-1 mb-3">{domain.description}</p>
                            <div className="d-flex justify-content-between align-items-center pt-2 border-top">
                              <span className="small text-muted" style={{ fontSize: '11px' }}>{domain.language || 'English'}</span>
                              <Link href={`/demo?agentId=${domain.id}`} className="btn btn-sm btn-outline-primary px-3 py-1 fw-semibold rounded-pill" style={{ fontSize: '12px' }}>
                                Try Demo Call
                              </Link>
                            </div>
                          </div>
                        </div>
                      </div>
                    ))}
                    
                    {/* NEED A CUSTOM AGENT CARD */}
                    <div className="carousel-item-custom flex-shrink-0">
                      <div className="card h-100 border-0 text-white" style={{ background: 'linear-gradient(135deg, #0f172a, #1e1b4b)', borderRadius: '10px' }}>
                        <div className="card-body p-3 d-flex flex-column justify-content-center text-center">
                          <div className="fs-3 mb-2">✨</div>
                          <h6 className="fw-bold mb-1">Need a Custom Agent?</h6>
                          <p className="text-light opacity-75 small mb-3">Custom workflows, brand-specific voices, and CRM integration.</p>
                          <button 
                            onClick={() => {
                              setRequestNotice(null);
                              setShowRequestModal(true);
                            }} 
                            className="btn btn-light btn-sm w-100 fw-bold py-2 rounded-pill mt-auto"
                            style={{ fontSize: '12px' }}
                          >
                            Request Custom Setup
                          </button>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>
            
            <div className="card-footer bg-light border-0 py-3 text-muted small">
              Domain schemas are loaded dynamically. Run a Demo call on any flow structure.
            </div>
          </div>
        </div>

        {/* SYSTEM CREDITS & STATUS */}
        <div className="col-md-4">
          <div className="card border-0 shadow-sm h-100 text-white" style={{ background: 'linear-gradient(135deg, #1e3a8a, #2563eb)' }}>
            <div className="card-body p-4 d-flex flex-column justify-content-between">
              <div>
                <h6 className="fw-semibold mb-1 opacity-75">System Credits & Status</h6>
                <div className="d-flex align-items-baseline gap-2 mb-4">
                  <h2 className="fw-bold m-0">12,450</h2>
                  <span className="small opacity-75">outbound mins</span>
                </div>
              </div>
              
              <div className="border-top border-white-50 pt-3 mt-2">
                <div className="row g-2 text-center">
                  <div className="col-4 border-end border-white-50">
                    <div className="small opacity-75 text-uppercase fw-semibold" style={{ fontSize: '10px' }}>Available</div>
                    <div className="fw-bold">{domains.length || 6} Domains</div>
                  </div>
                  <div className="col-4 border-end border-white-50">
                    <div className="small opacity-75 text-uppercase fw-semibold" style={{ fontSize: '10px' }}>Demo Remaining</div>
                    <div className="fw-bold">60 Mins</div>
                  </div>
                  <div className="col-4">
                    <div className="small opacity-75 text-uppercase fw-semibold" style={{ fontSize: '10px' }}>Assigned</div>
                    <div className="fw-bold">{availableAgents.length || 1} Agents</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* RECENT ACTIVITY SECTION */}
      <div className="card border-0 shadow-sm mb-4">
        <div className="card-header bg-white border-bottom py-3 d-flex justify-content-between align-items-center">
          <div>
            <h6 className="mb-0 fw-bold">Recent Activity</h6>
            <div className="small text-muted">
              Unified logs for campaign launches, custom request submissions, and demo call sessions.
              {shouldAutoRefreshCampaigns && <span className="text-success fw-semibold ms-2">Auto-refresh on</span>}
              {lastActivityRefresh && <span className="ms-2">Updated {lastActivityRefresh}</span>}
            </div>
          </div>
          <button className="btn btn-sm btn-outline-secondary" type="button" onClick={loadRecentActivity} disabled={activityLoading}>
            {activityLoading ? 'Refreshing...' : 'Refresh'}
          </button>
        </div>
        <div className="table-responsive">
          <table className="table table-hover align-middle mb-0">
            <thead className="table-light text-muted small">
              <tr>
                <th className="fw-semibold px-4 py-3">Activity</th>
                <th className="fw-semibold py-3">Type</th>
                <th className="fw-semibold py-3">Status</th>
                <th className="fw-semibold py-3">Details</th>
                <th className="fw-semibold py-3 text-end pe-4">Action</th>
              </tr>
            </thead>
            <tbody>
              {activityLoading && recentActivities.length === 0 ? (
                <tr>
                  <td colSpan="5" className="text-center py-4 text-muted">
                    <span className="spinner-border spinner-border-sm me-2" role="status" />
                    Loading activities...
                  </td>
                </tr>
              ) : recentActivities.length === 0 ? (
                <tr>
                  <td colSpan="5" className="text-center py-4 text-muted">
                    No activities recorded yet.
                  </td>
                </tr>
              ) : recentActivities.map((activity) => (
                <tr key={activity.id}>
                  <td className="px-4 py-3">
                    <div className="fw-bold text-dark">{activity.title}</div>
                    <div className="text-muted small" style={{ fontSize: '11px' }}>ID: {activity.id}</div>
                  </td>
                  <td className="py-3">
                    <span className={`badge ${
                      activity.type === 'campaign' 
                        ? 'bg-primary-subtle text-primary border border-primary-subtle' 
                        : activity.type === 'demo_call'
                          ? 'bg-success-subtle text-success border border-success-subtle'
                          : 'bg-warning-subtle text-warning-emphasis border border-warning-subtle'
                    }`}>
                      {activity.type === 'campaign' ? 'Outbound Campaign' : activity.type === 'demo_call' ? 'Demo Call' : 'Custom Request'}
                    </span>
                  </td>
                  <td className="py-3">
                    <span className={`badge ${
                      ['Active', 'Demo Scheduled', 'Converted'].includes(activity.status)
                        ? 'bg-success text-white'
                        : ['Done', 'Contacted'].includes(activity.status)
                          ? 'bg-info text-dark'
                          : 'bg-light text-secondary border'
                    }`}>
                      {activity.status}
                    </span>
                  </td>
                  <td className="py-3 small text-secondary fw-medium">{activity.detail}</td>
                  <td className="py-3 text-end pe-4">
                    {activity.type !== 'custom_request' ? (
                      <Link className="btn btn-sm btn-outline-primary px-3 rounded-pill" href={`/results?campaign=${encodeURIComponent(activity.id)}`}>
                        View Results
                      </Link>
                    ) : (
                      <span className="text-muted small fst-italic">Request filed</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* LIVE EVENT BOXES IF ENABLED */}
      {clientLiveEventsEnabled && (
        <div className="card border-0 shadow-sm mb-4">
          <div className="card-header bg-white border-bottom py-3 d-flex justify-content-between align-items-center">
            <div>
              <h6 className="mb-0 fw-bold">Live Activity Feed</h6>
              <div className="small text-muted">Latest live routing signals and campaign updates.</div>
            </div>
            <span className={`badge ${
              liveSocketStatus === 'connected'
                ? 'bg-success-subtle text-success border border-success-subtle'
                : liveSocketStatus === 'error'
                  ? 'bg-danger-subtle text-danger border border-danger-subtle'
                  : 'bg-light text-secondary border'
            }`}>
              {liveSocketStatus}
            </span>
          </div>
          <div className="table-responsive">
            <table className="table table-hover align-middle mb-0">
              <thead className="table-light text-muted small">
                <tr>
                  <th className="fw-semibold px-4 py-3">Time</th>
                  <th className="fw-semibold py-3">Event</th>
                  <th className="fw-semibold py-3">Lead</th>
                  <th className="fw-semibold py-3">Status</th>
                  <th className="fw-semibold py-3">Provider</th>
                </tr>
              </thead>
              <tbody>
                {liveEvents.length === 0 ? (
                  <tr>
                    <td colSpan="5" className="text-center py-4 text-muted">
                      Waiting for live events...
                    </td>
                  </tr>
                ) : liveEvents.map((event) => (
                  <tr key={event.key}>
                    <td className="px-4 py-3 small text-muted">{event.receivedAt}</td>
                    <td className="py-3 fw-semibold">{event.label}</td>
                    <td className="py-3">{event.leadName}</td>
                    <td className="py-3">
                      <span className="badge bg-light text-secondary border">{event.status || event.snippet || 'Update'}</span>
                    </td>
                    <td className="py-3">{getProviderLabel('telephony', event.provider || selectedProvider)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* LAUNCH OUTBOUND CAMPAIGN MODAL */}
      {showLaunchModal && (
        <div className="modal show d-block" tabIndex="-1" style={{ backgroundColor: 'rgba(15,23,42,0.45)' }}>
          <div className="modal-dialog modal-lg modal-dialog-centered">
            <div className="modal-content border-0 shadow">
              <div className="modal-header border-bottom">
                <div>
                  <h5 className="modal-title fw-bold">Launch Outbound Campaign</h5>
                  <div className="small text-muted">{agentName} will call the leads you add below.</div>
                </div>
                <button type="button" className="btn-close shadow-none" onClick={() => setShowLaunchModal(false)} />
              </div>
              <form onSubmit={launchCampaign}>
                <div className="modal-body">
                  <div className="row g-3 mb-3">
                    <div className="col-md-7">
                      <label className="form-label small fw-bold">Campaign Name</label>
                      <input
                        type="text"
                        className="form-control shadow-none"
                        value={form.campaignName}
                        onChange={(e) => setForm((current) => ({ ...current, campaignName: e.target.value }))}
                        placeholder="May site visit follow-up"
                        required
                      />
                    </div>
                    <div className="col-md-5">
                      <label className="form-label small fw-bold">Calling Mode</label>
                      <select
                        className="form-select shadow-none"
                        value={selectedProvider}
                        onChange={(e) => setForm((current) => ({ ...current, telephonyProvider: e.target.value }))}
                      >
                        {providerOptions.map((provider) => (
                          <option key={provider} value={provider}>
                            {provider === 'demo' ? 'Demo calling' : getProviderLabel('telephony', provider)}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>

                  <div className="border rounded-3 overflow-hidden">
                    <div className="d-flex justify-content-between align-items-center px-3 py-2 bg-light border-bottom">
                      <span className="small fw-bold">Lead Details</span>
                      <div className="d-flex gap-2 align-items-center">
                        <label className="btn btn-sm btn-outline-secondary mb-0">
                          Upload CSV
                          <input type="file" accept=".csv,text/csv" hidden onChange={handleCsvUpload} />
                        </label>
                        <button type="button" className="btn btn-sm btn-outline-primary" onClick={addLeadRow}>
                          Add Lead
                        </button>
                      </div>
                    </div>
                    <div className="p-3">
                      {csvError && (
                        <div className="alert alert-danger py-2 small mb-3">{csvError}</div>
                      )}
                      {csvLeads.length > 0 && (
                        <div className="border rounded-3 mb-3 overflow-hidden">
                          <div className="d-flex justify-content-between align-items-center bg-success-subtle px-3 py-2 border-bottom">
                            <div>
                              <div className="small fw-bold text-success">{csvLeads.length} CSV leads loaded</div>
                              <div className="small text-muted">{csvFileName}</div>
                            </div>
                            <button type="button" className="btn btn-sm btn-outline-secondary" onClick={clearCsv}>
                              Clear CSV
                            </button>
                          </div>
                          <div className="table-responsive">
                            <table className="table table-sm mb-0">
                              <tbody>
                                {csvLeads.slice(0, 5).map((lead, index) => (
                                  <tr key={`${lead.phone}-${index}`}>
                                    <td className="px-3 text-muted small">{lead.name}</td>
                                    <td className="px-3 text-muted small">{lead.phone}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </div>
                      )}
                      {leadRows.map((lead, index) => (
                        <div key={index} className="row g-2 align-items-center mb-2">
                          <div className="col-md-5">
                            <input
                              type="text"
                              className="form-control shadow-none"
                              value={lead.name}
                              onChange={(e) => updateLead(index, 'name', e.target.value)}
                              placeholder="Lead name"
                            />
                          </div>
                          <div className="col-md-5">
                            <input
                              type="tel"
                              className="form-control shadow-none"
                              value={lead.phone}
                              onChange={(e) => updateLead(index, 'phone', e.target.value)}
                              placeholder="+91XXXXXXXXXX"
                            />
                          </div>
                          <div className="col-md-2 text-end">
                            <button type="button" className="btn btn-sm btn-outline-secondary" onClick={() => removeLeadRow(index)}>
                              Remove
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
                <div className="modal-footer border-top">
                  <button type="button" className="btn btn-light border" onClick={() => setShowLaunchModal(false)} disabled={loading}>
                    Cancel
                  </button>
                  <button type="submit" className="btn btn-primary px-4" disabled={loading}>
                    {loading ? 'Launching...' : 'Start Calling'}
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}

      {/* REQUEST CUSTOM AGENT MODAL */}
      {showRequestModal && (
        <div className="modal show d-block" tabIndex="-1" style={{ backgroundColor: 'rgba(15,23,42,0.45)' }}>
          <div className="modal-dialog modal-dialog-centered">
            <div className="modal-content border-0 shadow">
              <div className="modal-header border-bottom">
                <h5 className="modal-title fw-bold">Request Custom AI Agent</h5>
                <button type="button" className="btn-close shadow-none" onClick={() => setShowRequestModal(false)} />
              </div>
              <form onSubmit={handleRequestSubmit}>
                <div className="modal-body">
                  {requestNotice && (
                    <div className={`alert alert-${requestNotice.type} py-2 small mb-3`} role="status">
                      {requestNotice.text}
                    </div>
                  )}

                  <div className="row g-3">
                    <div className="col-12">
                      <label className="form-label small fw-semibold">Your Name *</label>
                      <input
                        type="text"
                        className="form-control shadow-none"
                        value={requestForm.name}
                        onChange={(e) => setRequestForm(prev => ({ ...prev, name: e.target.value }))}
                        placeholder="John Doe"
                        required
                      />
                    </div>
                    
                    <div className="col-md-6">
                      <label className="form-label small fw-semibold">Company Name</label>
                      <input
                        type="text"
                        className="form-control shadow-none"
                        value={requestForm.company}
                        onChange={(e) => setRequestForm(prev => ({ ...prev, company: e.target.value }))}
                        placeholder="Acme Corp"
                      />
                    </div>
                    
                    <div className="col-md-6">
                      <label className="form-label small fw-semibold">Email Address *</label>
                      <input
                        type="email"
                        className="form-control shadow-none"
                        value={requestForm.email}
                        onChange={(e) => setRequestForm(prev => ({ ...prev, email: e.target.value }))}
                        placeholder="john@example.com"
                        required
                      />
                    </div>

                    <div className="col-md-6">
                      <label className="form-label small fw-semibold">Phone Number</label>
                      <input
                        type="tel"
                        className="form-control shadow-none"
                        value={requestForm.phone}
                        onChange={(e) => setRequestForm(prev => ({ ...prev, phone: e.target.value }))}
                        placeholder="+1 (555) 000-0000"
                      />
                    </div>

                    <div className="col-md-6">
                      <label className="form-label small fw-semibold">Industry</label>
                      <input
                        type="text"
                        className="form-control shadow-none"
                        value={requestForm.industry}
                        onChange={(e) => setRequestForm(prev => ({ ...prev, industry: e.target.value }))}
                        placeholder="Healthcare, Logistics, etc."
                      />
                    </div>

                    <div className="col-md-6">
                      <label className="form-label small fw-semibold">Monthly Volume</label>
                      <select
                        className="form-select shadow-none"
                        value={requestForm.monthlyVolume}
                        onChange={(e) => setRequestForm(prev => ({ ...prev, monthlyVolume: e.target.value }))}
                      >
                        <option value="< 1,000">&lt; 1,000 calls</option>
                        <option value="1,000 - 5,000">1,000 - 5,000 calls</option>
                        <option value="5,000 - 20,000">5,000 - 20,000 calls</option>
                        <option value="> 20,000">&gt; 20,000 calls</option>
                      </select>
                    </div>

                    <div className="col-md-6">
                      <label className="form-label small fw-semibold">Primary Use Case</label>
                      <input
                        type="text"
                        className="form-control shadow-none"
                        value={requestForm.useCase}
                        onChange={(e) => setRequestForm(prev => ({ ...prev, useCase: e.target.value }))}
                        placeholder="Lead qualification, support, etc."
                      />
                    </div>

                    <div className="col-12">
                      <label className="form-label small fw-semibold">Additional Requirements / Notes</label>
                      <textarea
                        rows="3"
                        className="form-control shadow-none"
                        value={requestForm.notes}
                        onChange={(e) => setRequestForm(prev => ({ ...prev, notes: e.target.value }))}
                        placeholder="Tell us more about scripts, voice accents, or systems to integrate with."
                      />
                    </div>
                  </div>
                </div>
                <div className="modal-footer border-top">
                  <button type="button" className="btn btn-light border" onClick={() => setShowRequestModal(false)} disabled={requestSubmitting}>
                    Close
                  </button>
                  <button type="submit" className="btn btn-primary px-4" disabled={requestSubmitting}>
                    {requestSubmitting ? 'Submitting...' : 'Submit Request'}
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}
    </DashboardLayout>
  );
}
