'use client';

import React from 'react';

export default function GenerateSummaryModal({ agent, draft, onClose }) {
  if (!agent) return null;

  let summary = '';
  if (draft?.conversationFlow?.global_prompt) {
    const match = draft.conversationFlow.global_prompt.match(/Complete Company Knowledge Summary:\n([\s\S]*)/);
    if (match) {
      summary = match[1];
    } else {
      summary = draft.conversationFlow.global_prompt;
    }
  } else if (draft?.knowledge?.company?.name) {
     summary = `* Company: ${draft.knowledge.company.name}\n\nNo detailed summary found.`;
  } else {
    summary = 'No summary available for this draft.';
  }

  return (
    <div className="modal show d-block" tabIndex="-1" style={{ backgroundColor: 'rgba(0,0,0,0.8)', backdropFilter: 'blur(4px)', zIndex: 1060 }}>
      <div className="modal-dialog modal-lg modal-dialog-centered modal-dialog-scrollable">
        <div className="modal-content border-0 shadow-lg" style={{ borderRadius: '12px' }}>
          
          <div className="modal-header border-bottom-0 pb-0">
            <div>
              <h5 className="modal-title fw-bold" style={{ color: '#000000', fontSize: '18px' }}>Generate Summary</h5>
              <div className="text-muted" style={{ fontSize: '14px' }}>
                {agent.name || 'Agent'} ({agent.tts_provider === 'indic_parler' ? 'Parler TTS' : agent.tts_provider})
              </div>
            </div>
            <button type="button" className="btn-close shadow-none" onClick={onClose}></button>
          </div>
          
          <div className="modal-body pt-3">
            <div className="d-flex justify-content-between align-items-center text-muted mb-3" style={{ fontSize: '14px', borderBottom: '1px solid #dee2e6', paddingBottom: '12px' }}>
              <span>None</span>
              <span className="fw-medium" style={{ color: '#000000' }}>5 nodes / shadow</span>
            </div>
            
            <div className="fw-semibold text-muted mb-2" style={{ fontSize: '14px' }}>Company Knowledge Summary</div>
            
            <div className="p-3 rounded border" style={{ maxHeight: '400px', overflowY: 'auto', backgroundColor: '#F9FAFB' }}>
              <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontFamily: 'inherit', fontSize: '14px', margin: 0, color: '#374151' }}>
                {summary}
              </pre>
            </div>
          </div>
          
          <div className="modal-footer border-top-0 pt-0">
            <button type="button" className="btn btn-outline-secondary btn-sm" onClick={onClose}>Close</button>
          </div>
          
        </div>
      </div>
    </div>
  );
}
