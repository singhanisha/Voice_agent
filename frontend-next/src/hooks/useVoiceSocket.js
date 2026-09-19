import { useState, useRef, useCallback } from 'react';

export function useVoiceSocket(agentId, activeClient) {
  const [isConnected, setIsConnected] = useState(false);
  const [statusText, setStatusText] = useState('Idle');
  const [transcripts, setTranscripts] = useState([]);
  const [events, setEvents] = useState([]);
  
  const wsRef = useRef(null);
  const audioCtxRef = useRef(null);
  const activeGainNodeRef = useRef(null);
  const micStreamRef = useRef(null);
  const micWorkletNodeRef = useRef(null);
  const micSilentGainRef = useRef(null);
  const nextStartTimeRef = useRef(0);
  const pingIntervalRef = useRef(null);
  const reconnectTimeoutRef = useRef(null);
  const shouldReconnectRef = useRef(false);
  const playbackReleaseTimeoutRef = useRef(null);
  const micBlockedUntilRef = useRef(0);
  
  // Statistical Jitter Tracking
  const lastPacketAtRef = useRef(0);
  const emaJitterRef = useRef(50); // Default 50ms jitter estimate
  const activeGenIdRef = useRef(0);
  const expectsGenHeaderRef = useRef(false);
  const lastMicStatsAtRef = useRef(0);

  const clearPlaybackReleaseTimer = useCallback(() => {
    if (playbackReleaseTimeoutRef.current) {
      clearTimeout(playbackReleaseTimeoutRef.current);
      playbackReleaseTimeoutRef.current = null;
    }
  }, []);

  const holdMicInput = useCallback((holdMs = 0) => {
    const now = performance.now();
    micBlockedUntilRef.current = Math.max(micBlockedUntilRef.current, now + holdMs);
    clearPlaybackReleaseTimer();
  }, [clearPlaybackReleaseTimer]);

  const scheduleMicResume = useCallback((ctx, tailMs = 120) => {
    const queuedMs = Math.max(0, (nextStartTimeRef.current - ctx.currentTime) * 1000);
    const releaseAfterMs = queuedMs + tailMs;
    holdMicInput(releaseAfterMs);
    playbackReleaseTimeoutRef.current = setTimeout(() => {
      const now = performance.now();
      if (now >= micBlockedUntilRef.current) {
        micBlockedUntilRef.current = 0;
      }
    }, releaseAfterMs);
  }, [holdMicInput]);

  const isMicInputBlocked = useCallback(() => (
    performance.now() < micBlockedUntilRef.current
  ), []);

  const cleanupMediaAndAudio = useCallback(() => {
    if (micWorkletNodeRef.current) {
      try {
        micWorkletNodeRef.current.port.onmessage = null;
        micWorkletNodeRef.current.disconnect();
      } catch (_) {}
      micWorkletNodeRef.current = null;
    }
    if (micSilentGainRef.current) {
      try {
        micSilentGainRef.current.disconnect();
      } catch (_) {}
      micSilentGainRef.current = null;
    }
    if (micStreamRef.current) {
      micStreamRef.current.getTracks().forEach(t => t.stop());
      micStreamRef.current = null;
    }
    if (audioCtxRef.current) {
      audioCtxRef.current.close().catch(() => {});
      audioCtxRef.current = null;
    }
    if (pingIntervalRef.current) {
      clearInterval(pingIntervalRef.current);
      pingIntervalRef.current = null;
    }
    clearPlaybackReleaseTimer();
    activeGainNodeRef.current = null;
    activeGenIdRef.current = 0;
    expectsGenHeaderRef.current = false;
    micBlockedUntilRef.current = 0;
    nextStartTimeRef.current = 0;
  }, [clearPlaybackReleaseTimer]);

  const toPcm16Buffer = useCallback((audioChunk) => {
    if (audioChunk instanceof ArrayBuffer) {
      return audioChunk;
    }
    if (audioChunk instanceof Int16Array) {
      return audioChunk.buffer.slice(audioChunk.byteOffset, audioChunk.byteOffset + audioChunk.byteLength);
    }

    const f32 = audioChunk instanceof Float32Array ? audioChunk : new Float32Array(audioChunk);
    const i16 = new Int16Array(f32.length);
    for (let i = 0; i < f32.length; i++) {
      const s = Math.max(-1, Math.min(1, f32[i]));
      i16[i] = s < 0 ? s * 32768 : s * 32767;
    }
    return i16.buffer;
  }, []);

  const logMicChunkStats = useCallback((audioChunk) => {
    let f32;
    if (audioChunk instanceof ArrayBuffer) {
      const i16 = new Int16Array(audioChunk);
      f32 = new Float32Array(i16.length);
      for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 32768;
    } else if (audioChunk instanceof Int16Array) {
      f32 = new Float32Array(audioChunk.length);
      for (let i = 0; i < audioChunk.length; i++) f32[i] = audioChunk[i] / 32768;
    } else {
      f32 = audioChunk instanceof Float32Array ? audioChunk : new Float32Array(audioChunk.buffer || audioChunk);
    }
    if (!f32.length) return;
    let energy = 0;
    for (let i = 0; i < f32.length; i++) energy += f32[i] * f32[i];
    const rms = Math.sqrt(energy / f32.length);
    const now = performance.now();
    if (now - lastMicStatsAtRef.current >= 1000) {
      console.debug('[VOICE] mic chunk', {
        samples: f32.length,
        rms: Number(rms.toFixed(4)),
        sampleRate: audioCtxRef.current?.sampleRate || 0,
        wsOpen: wsRef.current?.readyState === WebSocket.OPEN,
        micBlocked: isMicInputBlocked(),
      });
      lastMicStatsAtRef.current = now;
    }
  }, [isMicInputBlocked]);

  const disconnect = useCallback(() => {
    shouldReconnectRef.current = false;
    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.onerror = null;
      wsRef.current.onmessage = null;
      wsRef.current.close();
      wsRef.current = null;
    }
    cleanupMediaAndAudio();
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    
    setIsConnected(false);
    setStatusText('Session ended');
  }, [cleanupMediaAndAudio]);

  const connect = useCallback(async (isDemo = false, leadName = 'Demo User', isReconnect = false) => {
    if (!isReconnect) disconnect();
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    
    try {
      if (!isReconnect) setStatusText('Initialising audio...');
      
      const ctx = new (window.AudioContext || window.webkitAudioContext)(); // Use hardware rate for stability
      audioCtxRef.current = ctx;
      if (ctx.state === 'suspended') await ctx.resume();

      const newGain = ctx.createGain();
      newGain.gain.value = 1;
      newGain.connect(ctx.destination);
      activeGainNodeRef.current = newGain;

      let workletLoaded = false;

      // Implement AudioWorklet Load (REQUIRED for zero-stutter)
      try {
        await ctx.audioWorklet.addModule('/audio-worklet-processor.js');
        workletLoaded = true;
      } catch (e) {
        console.warn("AudioWorklet failed, falling back to ScriptProcessor (Degraded)", e);
      }

      const clientId = encodeURIComponent(activeClient);
      const encodedLead = encodeURIComponent(leadName);
      const endpoint = isDemo 
        ? `api/voice-demo?agentId=${encodeURIComponent(agentId)}&clientId=${clientId}&leadName=${encodedLead}`
        : `api/voice-live?agentId=${encodeURIComponent(agentId)}&leadName=${encodedLead}`;

      // Build WebSocket URL from the backend API URL.
      // In production, NEXT_PUBLIC_API_URL should be set to the Railway backend URL
      // (e.g. https://your-app.up.railway.app). In dev, fall back to localhost:8000.
      const API = process.env.NEXT_PUBLIC_API_URL || `http://localhost:8000`;
      const WS_URL = API.replace(/^https/, 'wss').replace(/^http/, 'ws').replace(/\/$/, '');
      const wsUrl = `${WS_URL}/${endpoint}`;
      console.log("WS URL:", wsUrl);
      const socket = new WebSocket(wsUrl);
      socket.binaryType = 'arraybuffer';
      wsRef.current = socket;
      shouldReconnectRef.current = true;

      socket.onopen = async () => {
        console.log("[WS] Connected to", wsUrl);
        setIsConnected(true);
        setStatusText('🔴 Listening — speak now');
        
        if (ctx && ctx.state === "suspended") {
          ctx.resume().then(() => {
            console.log("[FRONTEND] AudioContext resumed, state is now:", ctx.state);
          }).catch(err => console.error("[FRONTEND] AudioContext resume failed:", err));
        } else if (ctx) {
          console.log("[FRONTEND] AudioContext state on connect:", ctx.state);
        }
        
        if (!isReconnect) {
          setTranscripts([]);
          setEvents([]);
        }

        const mic = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
        });
        micStreamRef.current = mic;
        
        const source = ctx.createMediaStreamSource(mic);
        
        // Handshake: Tell server we are sending exactly 16kHz audio
        socket.send(JSON.stringify({ type: 'mic_ready', sampleRate: 16000 }));
        
        if (workletLoaded) {
          const workletNode = new AudioWorkletNode(ctx, 'mic-capture-processor');
          const silentGain = ctx.createGain();
          silentGain.gain.value = 0;
          source.connect(workletNode);
          workletNode.connect(silentGain);
          silentGain.connect(ctx.destination);
          micWorkletNodeRef.current = workletNode;
          micSilentGainRef.current = silentGain;
          workletNode.port.onmessage = (e) => {
            if (isMicInputBlocked()) return;
            if (wsRef.current?.readyState === WebSocket.OPEN) {
              logMicChunkStats(e.data);
              wsRef.current.send(toPcm16Buffer(e.data));
            }
          };
        } else {
          // Fallback if worklet failed
          const processor = ctx.createScriptProcessor(2048, 1, 1);
          const silentGain = ctx.createGain();
          silentGain.gain.value = 0;
          source.connect(processor);
          processor.connect(silentGain);
          silentGain.connect(ctx.destination);

          let buffer = [];
          let inputPointer = 0;
          const ratio = ctx.sampleRate / 16000;

          processor.onaudioprocess = (e) => {
            if (isMicInputBlocked()) return;
            const inp = e.inputBuffer.getChannelData(0);
            for (let i = 0; i < inp.length; i++) {
              buffer.push(inp[i]);
            }
            
            const outputSamples = [];
            while (inputPointer < buffer.length) {
              const index = Math.floor(inputPointer);
              const nextIndex = index + 1;
              const frac = inputPointer - index;
              
              const s0 = buffer[index];
              const s1 = nextIndex < buffer.length ? buffer[nextIndex] : s0;
              const interpolated = s0 + frac * (s1 - s0);
              outputSamples.push(interpolated);
              inputPointer += ratio;
            }
            
            const consumed = Math.floor(inputPointer);
            if (consumed > 0) {
              buffer = buffer.slice(consumed);
              inputPointer -= consumed;
            }

            if (outputSamples.length > 0) {
              const i16 = new Int16Array(outputSamples.length);
              for (let j = 0; j < outputSamples.length; j++) {
                const s = Math.max(-1, Math.min(1, outputSamples[j]));
                i16[j] = s < 0 ? s * 32768 : s * 32767;
              }
              logMicChunkStats(i16.buffer);
              if (wsRef.current?.readyState === WebSocket.OPEN) {
                wsRef.current.send(i16.buffer);
              }
            }
          };
        }

        pingIntervalRef.current = setInterval(() => {
          if (wsRef.current?.readyState === WebSocket.OPEN) {
            wsRef.current.send(JSON.stringify({ type: 'ping' }));
          }
        }, 5000);
      };

      socket.onmessage = (e) => {
        const liveCtx = audioCtxRef.current;
        if (!liveCtx) return;
        
        if (typeof e.data === 'string') {
          try {
            const msg = JSON.parse(e.data);
            if (msg.type === 'transcript') {
              setTranscripts(prev => [...prev, msg]);
            } else if (msg.type === 'cancel') {
              activeGenIdRef.current = (activeGenIdRef.current + 1);
              expectsGenHeaderRef.current = false;
              holdMicInput(120);

              if (activeGainNodeRef.current && liveCtx) {
                const ct = liveCtx.currentTime;
                const oldGain = activeGainNodeRef.current;
                oldGain.gain.cancelScheduledValues(ct);
                oldGain.gain.setValueAtTime(oldGain.gain.value, ct);
                oldGain.gain.linearRampToValueAtTime(0, ct + 0.05);

                const newGain = liveCtx.createGain();
                newGain.gain.value = 1;
                newGain.connect(liveCtx.destination);
                activeGainNodeRef.current = newGain;
              }
              nextStartTimeRef.current = 0;
            } else if (msg.type === 'gen_id') {
              activeGenIdRef.current = msg.value || (activeGenIdRef.current + 1);
              expectsGenHeaderRef.current = false;
              
              if (!activeGainNodeRef.current) {
                const newGain = liveCtx.createGain();
                newGain.gain.value = 1;
                newGain.connect(liveCtx.destination);
                activeGainNodeRef.current = newGain;
              }
              nextStartTimeRef.current = 0;
              if (liveCtx.state === 'suspended') {
                liveCtx.resume().catch(err => console.warn('[FRONTEND] ctx.resume on gen_id failed:', err));
              }
            } else if (msg.type?.startsWith('call_')) {
              setEvents(prev => [...prev, msg]);
            } else if (msg.type === 'ping') {
              // Server keepalive — reply with pong so the backend knows we're alive
              if (wsRef.current?.readyState === WebSocket.OPEN) {
                wsRef.current.send(JSON.stringify({ type: 'pong' }));
              }
            }
            // all other types (pong, etc.) are silently ignored
          } catch (_) {
            // plain text (e.g. "pong") — ignore silently
          }
          return;
        }

        // Binary PCM from server (supports both with/without 4-byte GenID header).
        if (e.data.byteLength < 2) return;
        let payloadOffset = 0;
        let incomingGenId = null;
        if (expectsGenHeaderRef.current && e.data.byteLength > 4) {
          const view = new DataView(e.data);
          incomingGenId = view.getInt32(0, true);
          if (incomingGenId < activeGenIdRef.current) return;
          payloadOffset = 4;
        }

        const payloadBytes = e.data.byteLength - payloadOffset;
        if (payloadBytes < 2) return;
        console.log(`[FRONTEND] Audio Chunk Received: ${e.data.byteLength} bytes (payload: ${payloadBytes} bytes) genId=${incomingGenId}`);
        const pcmData = new Int16Array(e.data, payloadOffset, Math.floor(payloadBytes / 2));
        if (pcmData.length === 0) return; // Guard against empty audio chunks

        // ── CRITICAL: Resume AudioContext if suspended (browser autoplay policy) ──
        // The AudioContext may start suspended after creation outside a direct user
        // gesture. We must resume it before scheduling any audio buffers, otherwise
        // all scheduled buffers pile up silently and nothing plays.
        if (liveCtx.state === 'suspended') {
          liveCtx.resume().then(() => {
            console.log('[FRONTEND] AudioContext resumed on audio receive, state:', liveCtx.state);
          }).catch(err => console.warn('[FRONTEND] ctx.resume on binary audio failed:', err));
        }

        // Guard: ensure gain node is still alive before connecting
        if (!activeGainNodeRef.current) {
          const recoveryGain = liveCtx.createGain();
          recoveryGain.gain.value = 1;
          recoveryGain.connect(liveCtx.destination);
          activeGainNodeRef.current = recoveryGain;
          console.warn('[FRONTEND] Gain node was null — recreated recovery gain node');
        }

        const floatData = new Float32Array(pcmData.length);
        for (let i = 0; i < pcmData.length; i++) floatData[i] = pcmData[i] / 32768;
        console.log(`[FRONTEND] Audio Decoded: Float32Array length=${floatData.length}. Buffer scheduling next.`);
        
        // 4. STATISTICAL ADAPTIVE JITTER BUFFER
        const nowReal = performance.now();
        if (lastPacketAtRef.current > 0) {
          const delta = nowReal - lastPacketAtRef.current;
          const jitter = Math.abs(delta - 20); // ideal interval 20ms
          emaJitterRef.current = emaJitterRef.current * 0.9 + jitter * 0.1;
        }
        lastPacketAtRef.current = nowReal;

        const buf = liveCtx.createBuffer(1, floatData.length, 24000);
        buf.getChannelData(0).set(floatData);
        const src = liveCtx.createBufferSource();
        src.buffer = buf;
        src.connect(activeGainNodeRef.current);
        
        const ctxNow = liveCtx.currentTime;
        // Increase lead time to 80ms minimum to prevent breaking/pops
        const adaptiveLead = Math.min(0.5, Math.max(0.08, (emaJitterRef.current / 1000) * 3.0));
        
        if (nextStartTimeRef.current < ctxNow) {
          nextStartTimeRef.current = ctxNow + adaptiveLead;
        }
        
        console.log(`[FRONTEND] AudioContext State=${liveCtx.state} ctxNow=${ctxNow.toFixed(3)} nextStartTime=${nextStartTimeRef.current.toFixed(3)} adaptiveLead=${adaptiveLead.toFixed(3)}`);
        
        src.start(nextStartTimeRef.current);
        console.log(`[FRONTEND] Audio Played chunk at ${nextStartTimeRef.current.toFixed(3)}`);
        nextStartTimeRef.current += buf.duration;
        scheduleMicResume(liveCtx);
      };

      socket.onclose = (event) => {
        console.log("WebSocket closed", event?.code, event?.reason);
        shouldReconnectRef.current = false;
        cleanupMediaAndAudio();
        wsRef.current = null;
        setIsConnected(false);
        setStatusText('Disconnected. Tap Start again.');
      };
      
      socket.onerror = (err) => {
        console.error("WebSocket error", err);
        setStatusText('Voice server error.');
        disconnect();
      };

    } catch (err) {
      console.error(err);
      setStatusText('Mic access denied or server unreachable.');
      disconnect();
    }
  }, [agentId, activeClient, cleanupMediaAndAudio, disconnect, holdMicInput, isMicInputBlocked, logMicChunkStats, scheduleMicResume, toPcm16Buffer]);

  const clearTranscripts = () => setTranscripts([]);

  return { connect, disconnect, isConnected, statusText, transcripts, events, clearTranscripts };
}
