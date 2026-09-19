'use client';
import { useAuth, clientProfile } from '@/context/AuthContext';
import DashboardLayout from '@/components/DashboardLayout';
import { useVoiceSocket } from '@/hooks/useVoiceSocket';
import { Mic, Bot, User } from 'lucide-react';

export default function TalkLive() {
  const { activeClient, currentRole, user } = useAuth();
  const profile = currentRole === 'client' && user?.agentId
    ? { name: user.clientName || user.name, agent: user.agentName || 'Assigned Agent', agentId: user.agentId }
    : clientProfile[activeClient];
  const agentId = profile?.agentId || user?.agentId || 'default';
  
  const { connect, disconnect, isConnected, statusText, transcripts, clearTranscripts } = useVoiceSocket(agentId, activeClient);

  const toggleCall = () => {
    if (isConnected) disconnect();
    else connect(false, user?.name || profile?.name || 'Demo User'); // isDemo = false
  };

  return (
    <DashboardLayout>
      <div className="d-flex justify-content-between align-items-start mb-4">
        <div>
          <h2 className="h4 fw-bold mb-1">Talk Live</h2>
          <p className="text-muted small mb-0">Test your agent ({profile?.agent}) directly from your browser mic instantly.</p>
        </div>
        <div 
          className="rounded-circle mt-2" 
          style={{ width: '12px', height: '12px', background: isConnected ? '#10b981' : '#94a3b8' }} 
          title={isConnected ? 'Connected' : 'Not connected'}
        />
      </div>

      <div className="row g-4">
        <div className="col-md-4">
          <div className="card border-0">
            <div className="card-body text-center p-5">
              <div 
                className="d-flex align-items-center justify-content-center mx-auto mb-4 text-white"
                style={{ 
                  width: '90px', height: '90px', fontSize: '32px', borderRadius: '50%', 
                  background: 'var(--color-black)',
                  boxShadow: isConnected ? '0 0 0 4px var(--color-live)' : 'none',
                  transition: 'box-shadow 0.4s ease'
                }}
              >
                <Mic size={40} className="text-white" />
              </div>
              <h5 className="fw-bold text-primary">{profile?.agent}</h5>
              <p className="text-muted small mb-4">{statusText}</p>
              <button 
                className={`btn btn-lg w-100 fw-bold border-0 ${isConnected ? 'btn-danger' : 'btn-dark'}`}
                onClick={toggleCall}
              >
                {isConnected ? 'End Conversation' : 'Connect & Start Talking'}
              </button>
            </div>
            <div className="card-footer bg-white border-top border-0 p-3 text-center">
              <div className="small text-muted">Use this tab for sandbox testing before launching campaigns.</div>
            </div>
          </div>
        </div>

        <div className="col-md-8">
          <div className="card border-0 h-100 d-flex flex-column">
            <div className="card-header bg-white border-bottom py-3 d-flex justify-content-between align-items-center">
              <h6 className="mb-0 fw-bold">Live Transcript</h6>
              <button className="btn btn-sm btn-link text-muted text-decoration-none shadow-none" onClick={clearTranscripts}>Clear</button>
            </div>
            <div className="card-body p-4 overflow-auto flex-grow-1" style={{ minHeight: '400px', background: 'var(--color-white)' }}>
              {transcripts.length === 0 && <div className="text-center text-muted small py-5">Speech will appear here as you talk</div>}
              {transcripts.map((msg, i) => {
                const isAgent = msg.speaker === 'agent';
                return (
                  <div key={i} className={`p-3 rounded-3 mb-2 border ${isAgent ? 'bg-white text-dark border-start border-dark border-4' : 'bg-light text-dark border-start border-secondary border-4'}`}>
                    <div className="small text-uppercase fw-bold text-muted mb-1" style={{ letterSpacing: '0.5px', fontSize: '10px' }}>
                      {isAgent ? <><Bot size={12} /> Agent</> : <><User size={12} /> You</>}
                    </div>
                    <div className="small">{msg.text}</div>
                  </div>
                );
              })}
            </div>
            <div className="card-footer bg-white border-top p-3 small text-muted text-center">
              Low-latency browser audio via Google Cloud Speech-to-Text Enterprise.
            </div>
          </div>
        </div>
      </div>
    </DashboardLayout>
  );
}
