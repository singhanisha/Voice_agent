'use client';

import { useEffect } from 'react';
import { Mic, Bot, User } from 'lucide-react';
import { useVoiceSocket } from '@/hooks/useVoiceSocket';

/**
 * TalkLiveModal
 * Lets the user actually CALL a specific agent from the browser mic and
 * hear it talk back in real time (reuses the same useVoiceSocket hook /
 * /api/voice-live websocket that the existing "Talk Live" page uses,
 * just scoped to one agent and rendered as a modal instead of a page).
 */
export default function TalkLiveModal({ agent, userName, onClose }) {
  const { connect, disconnect, isConnected, statusText, transcripts, clearTranscripts } =
    useVoiceSocket(agent?.id, agent?.client_id);

  // Always hang up + release the mic when the modal closes/unmounts.
  useEffect(() => () => disconnect(), [disconnect]);

  const toggleCall = () => {
    if (isConnected) disconnect();
    else connect(false, userName || 'Test User');
  };

  const handleClose = () => {
    disconnect();
    onClose();
  };

  return (
    <div className="modal show d-block" tabIndex="-1" style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}>
      <div className="modal-dialog modal-lg modal-dialog-centered modal-dialog-scrollable">
        <div className="modal-content border-0 shadow">
          <div className="modal-header">
            <h5 className="modal-title fw-bold">Test Run &mdash; {agent?.name}</h5>
            <button type="button" className="btn-close" onClick={handleClose} />
          </div>
          <div className="modal-body">
            <div className="row g-4">
              <div className="col-md-4">
                <div className="card border-0">
                  <div className="card-body text-center p-4">
                    <div
                      className="d-flex align-items-center justify-content-center mx-auto mb-3 text-white"
                      style={{
                        width: '80px',
                        height: '80px',
                        borderRadius: '50%',
                        background: '#000',
                        boxShadow: isConnected ? '0 0 0 4px #10b981' : 'none',
                        transition: 'box-shadow 0.4s ease',
                      }}
                    >
                      <Mic size={34} />
                    </div>
                    <p className="text-muted small mb-3">{statusText}</p>
                    <button
                      className={`btn btn-lg w-100 fw-bold border-0 ${isConnected ? 'btn-danger' : 'btn-dark'}`}
                      onClick={toggleCall}
                    >
                      {isConnected ? 'End Call' : '🎙️ Connect & Start Talking'}
                    </button>
                    <div className="small text-muted mt-3">
                      Allow microphone access when your browser asks for it.
                    </div>
                  </div>
                </div>
              </div>

              <div className="col-md-8">
                <div className="card border-0 h-100 d-flex flex-column">
                  <div className="card-header bg-white border-bottom py-2 d-flex justify-content-between align-items-center">
                    <h6 className="mb-0 fw-bold small">Live Transcript</h6>
                    <button className="btn btn-sm btn-link text-muted text-decoration-none" onClick={clearTranscripts}>
                      Clear
                    </button>
                  </div>
                  <div className="card-body p-3 overflow-auto flex-grow-1" style={{ minHeight: '320px', maxHeight: '420px' }}>
                    {transcripts.length === 0 && (
                      <div className="text-center text-muted small py-5">Speech will appear here as you talk</div>
                    )}
                    {transcripts.map((msg, i) => {
                      const isAgent = msg.speaker === 'agent';
                      return (
                        <div
                          key={i}
                          className={`p-2 rounded-3 mb-2 border ${isAgent ? 'bg-white border-start border-dark border-4' : 'bg-light border-start border-secondary border-4'}`}
                        >
                          <div className="small text-uppercase fw-bold text-muted mb-1" style={{ fontSize: '10px' }}>
                            {isAgent ? <><Bot size={12} /> Agent</> : <><User size={12} /> You</>}
                          </div>
                          <div className="small">{msg.text}</div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>
            </div>
          </div>
          <div className="modal-footer border-top">
            <button type="button" className="btn btn-light border" onClick={handleClose}>
              Close
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}