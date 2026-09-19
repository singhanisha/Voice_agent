'use client';
import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/context/AuthContext';
import { auth } from '@/lib/firebase';
import {
  signInWithEmailAndPassword,
  createUserWithEmailAndPassword,
  signInWithPopup,
  GoogleAuthProvider,
  sendPasswordResetEmail,
} from 'firebase/auth';

const googleProvider = new GoogleAuthProvider();

export default function LoginPage() {
  const { currentRole, loading } = useAuth();
  const router = useRouter();

  const [mode, setMode] = useState('login'); // 'login' | 'signup' | 'reset'
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [showPass, setShowPass] = useState(false);

  // Redirect if already logged in
  useEffect(() => {
    if (!loading && currentRole === 'admin') router.push('/monitor');
    if (!loading && currentRole === 'client') router.push('/client-dashboard');
  }, [currentRole, loading, router]);

  if (loading || currentRole) return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', background: 'var(--color-white)' }}>
      <div className="cc-spinner" />
    </div>
  );

  const clearMessages = () => { setError(''); setInfo(''); };

  // ── Email / Password handlers ────────────────────────────────────────────
  const handleEmailAuth = async (e) => {
    e.preventDefault();
    clearMessages();
    if (mode === 'signup' && password !== confirmPassword) {
      setError('Passwords do not match.');
      return;
    }
    setSubmitting(true);
    try {
      if (mode === 'login') {
        await signInWithEmailAndPassword(auth, email, password);
      } else {
        await createUserWithEmailAndPassword(auth, email, password);
      }
      // onAuthStateChanged in AuthContext handles redirect
    } catch (err) {
      setError(friendlyError(err.code));
    } finally {
      setSubmitting(false);
    }
  };

  const handleReset = async (e) => {
    e.preventDefault();
    clearMessages();
    if (!email) { setError('Enter your email address first.'); return; }
    setSubmitting(true);
    try {
      await sendPasswordResetEmail(auth, email);
      setInfo('Reset link sent! Check your inbox.');
      setMode('login');
    } catch (err) {
      setError(friendlyError(err.code));
    } finally {
      setSubmitting(false);
    }
  };

  // ── Google Sign-in ───────────────────────────────────────────────────────
  const handleGoogle = async () => {
    clearMessages();
    setSubmitting(true);
    try {
      await signInWithPopup(auth, googleProvider);
      // Account is auto-created if it doesn't exist
    } catch (err) {
      if (err.code !== 'auth/popup-closed-by-user') {
        setError(friendlyError(err.code));
      }
    } finally {
      setSubmitting(false);
    }
  };

  // ── Error messages ───────────────────────────────────────────────────────
  function friendlyError(code) {
    const map = {
      'auth/user-not-found':        'No account found with this email.',
      'auth/wrong-password':        'Incorrect password. Try again.',
      'auth/email-already-in-use':  'An account with this email already exists.',
      'auth/weak-password':         'Password must be at least 6 characters.',
      'auth/invalid-email':         'Please enter a valid email address.',
      'auth/too-many-requests':     'Too many attempts. Please try again later.',
      'auth/network-request-failed':'Network error. Check your connection.',
      'auth/invalid-credential':    'Invalid email or password.',
    };
    return map[code] || 'Something went wrong. Please try again.';
  }

  return (
    <>
      <style>{`
        .cc-login-root {
          min-height: 100vh;
          background: var(--color-white);
          display: flex;
          align-items: center;
          justify-content: center;
          font-family: var(--font-sans, system-ui, sans-serif);
          padding: 24px;
        }

        .cc-card {
          position: relative;
          width: 100%;
          max-width: 400px;
          background: var(--color-cream);
          border: 1px solid var(--color-border);
          border-radius: 24px;
          padding: 36px 32px;
          animation: cc-fade-in 150ms ease-out forwards;
        }
        @keyframes cc-fade-in {
          from { opacity: 0; }
          to   { opacity: 1; }
        }

        /* logo */
        .cc-logo { text-align: center; margin-bottom: 28px; }
        .cc-logo-icon {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 48px; height: 48px;
          border-radius: 50%;
          background: var(--color-black);
          border: 1px solid var(--color-black);
          font-size: 24px;
          margin-bottom: 12px;
        }
        .cc-brand { font-size: 20px; font-weight: 600; color: var(--color-black); letter-spacing: -0.2px; }
        .cc-brand span { color: var(--color-text-muted); font-weight: 500; }
        .cc-tagline { font-size: 11px; color: var(--color-text-faint); margin-top: 4px; letter-spacing: 0.06em; text-transform: uppercase; font-family: var(--font-mono, monospace); }

        /* mode tabs */
        .cc-tabs { display: flex; background: var(--color-white); border: 1px solid var(--color-border); border-radius: 999px; padding: 3px; margin-bottom: 24px; gap: 3px; }
        .cc-tab {
          flex: 1; padding: 7px; text-align: center; font-size: 13px; font-weight: 500;
          color: var(--color-text-faint); border-radius: 999px; cursor: pointer; transition: background-color 100ms ease-out, color 100ms ease-out;
          user-select: none; border: none; background: none; font-family: var(--font-mono, monospace); text-transform: uppercase; letter-spacing: 0.05em;
        }
        .cc-tab.active { background: var(--color-black); color: var(--color-white); }

        /* form */
        .cc-label { display: block; font-size: 11px; font-weight: 500; color: var(--color-text-faint); margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.06em; font-family: var(--font-mono, monospace); }
        .cc-input-wrap { position: relative; margin-bottom: 12px; }
        .cc-input {
          width: 100%; padding: 10px 12px; border-radius: 8px;
          background: var(--color-white); border: 1px solid var(--color-border);
          color: var(--color-black); font-size: 14px; font-family: inherit; outline: none;
          transition: border-color 100ms ease-out;
          box-sizing: border-box;
        }
        .cc-input::placeholder { color: var(--color-text-faint); }
        .cc-input:focus { border-color: var(--color-black); }
        .cc-input-icon { position: absolute; right: 12px; top: 50%; transform: translateY(-50%); cursor: pointer; color: var(--color-text-faint); font-size: 16px; user-select: none; transition: color 100ms ease-out; }
        .cc-input-icon:hover { color: var(--color-text-muted); }

        /* buttons */
        .cc-btn {
          width: 100%; padding: 12px; border-radius: 999px; font-size: 13px; font-weight: 500;
          font-family: var(--font-mono, monospace); cursor: pointer; transition: opacity 100ms ease-out; border: none;
          display: flex; align-items: center; justify-content: center; gap: 8px; text-transform: uppercase; letter-spacing: 0.05em;
        }
        .cc-btn-primary {
          background: var(--color-black);
          color: var(--color-white);
        }
        .cc-btn-primary:hover:not(:disabled) { opacity: 0.8; }
        .cc-btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }

        .cc-btn-google {
          background: var(--color-white); color: var(--color-black);
          border: 1px solid var(--color-border);
          margin-bottom: 0;
        }
        .cc-btn-google:hover:not(:disabled) { opacity: 0.8; }
        .cc-btn-google:disabled { opacity: 0.5; cursor: not-allowed; }

        /* divider */
        .cc-divider { display: flex; align-items: center; gap: 12px; margin: 16px 0; }
        .cc-divider-line { flex: 1; height: 1px; background: var(--color-border); }
        .cc-divider-text { font-size: 11px; color: var(--color-text-faint); font-weight: 500; font-family: var(--font-mono, monospace); text-transform: uppercase; }

        /* alerts */
        .cc-error { background: rgba(248,113,113,0.08); border: 1px solid #F87171; border-radius: 6px; padding: 10px 12px; color: #DC2626; font-size: 13px; margin-bottom: 12px; }
        .cc-info  { background: rgba(74,222,128,0.08);  border: 1px solid #4ADE80; border-radius: 6px; padding: 10px 12px; color: #16A34A;  font-size: 13px; margin-bottom: 12px; }

        /* forgot */
        .cc-forgot { text-align: right; margin-top: -6px; margin-bottom: 12px; }
        .cc-link { background: none; border: none; color: var(--color-text-faint); font-size: 12px; font-weight: 500; cursor: pointer; padding: 0; font-family: inherit; transition: color 100ms ease-out; }
        .cc-link:hover { color: var(--color-black); text-decoration: none; }

        /* footer */
        .cc-footer { text-align: center; margin-top: 20px; font-size: 11px; color: var(--color-text-faint); font-family: var(--font-mono, monospace); }

        /* Google icon SVG */
        .cc-google-icon { width: 18px; height: 18px; }
      `}</style>

      <div className="cc-login-root">

        <div className="cc-card">
          {/* Logo */}
          <div className="cc-logo">
            <div className="cc-logo-icon">🦎</div>
            <div className="cc-brand">Cosmic <span>Chameleon</span></div>
            <div className="cc-tagline">Voice Agent Platform v2.0</div>
          </div>

          {/* Mode tabs — only show for login/signup */}
          {mode !== 'reset' && (
            <div className="cc-tabs">
              <button className={`cc-tab ${mode === 'login' ? 'active' : ''}`} onClick={() => { setMode('login'); clearMessages(); }}>
                Sign In
              </button>
              <button className={`cc-tab ${mode === 'signup' ? 'active' : ''}`} onClick={() => { setMode('signup'); clearMessages(); }}>
                Sign Up
              </button>
            </div>
          )}

          {/* Reset mode heading */}
          {mode === 'reset' && (
            <div style={{ textAlign: 'center', marginBottom: '20px' }}>
              <div style={{ color: '#fff', fontWeight: 700, fontSize: '18px', marginBottom: '6px' }}>Reset Password</div>
              <div style={{ color: 'rgba(255,255,255,0.4)', fontSize: '13px' }}>We&apos;ll send a link to your email</div>
            </div>
          )}

          {/* Error / Info */}
          {error && <div className="cc-error">⚠️ {error}</div>}
          {info  && <div className="cc-info">✅ {info}</div>}

          {/* ── Form ── */}
          <form onSubmit={mode === 'reset' ? handleReset : handleEmailAuth} autoComplete="on">
            <label className="cc-label" htmlFor="cc-email">Email</label>
            <div className="cc-input-wrap">
              <input
                id="cc-email"
                type="email"
                className="cc-input"
                placeholder="you@company.com"
                value={email}
                onChange={e => setEmail(e.target.value)}
                required
                autoComplete="email"
              />
            </div>

            {mode !== 'reset' && (
              <>
                <label className="cc-label" htmlFor="cc-password">Password</label>
                <div className="cc-input-wrap">
                  <input
                    id="cc-password"
                    type={showPass ? 'text' : 'password'}
                    className="cc-input"
                    style={{ paddingRight: '40px' }}
                    placeholder={mode === 'signup' ? 'Min 6 characters' : '••••••••'}
                    value={password}
                    onChange={e => setPassword(e.target.value)}
                    required
                    autoComplete={mode === 'signup' ? 'new-password' : 'current-password'}
                  />
                  <span className="cc-input-icon" onClick={() => setShowPass(p => !p)} title={showPass ? 'Hide' : 'Show'}>
                    {showPass ? '🙈' : '👁️'}
                  </span>
                </div>
              </>
            )}

            {mode === 'signup' && (
              <>
                <label className="cc-label" htmlFor="cc-confirm">Confirm Password</label>
                <div className="cc-input-wrap">
                  <input
                    id="cc-confirm"
                    type={showPass ? 'text' : 'password'}
                    className="cc-input"
                    placeholder="Re-enter password"
                    value={confirmPassword}
                    onChange={e => setConfirmPassword(e.target.value)}
                    required
                    autoComplete="new-password"
                  />
                </div>
              </>
            )}

            {mode === 'login' && (
              <div className="cc-forgot">
                <button type="button" className="cc-link" onClick={() => { setMode('reset'); clearMessages(); }}>
                  Forgot password?
                </button>
              </div>
            )}

            {mode === 'reset' && (
              <div className="cc-forgot">
                <button type="button" className="cc-link" onClick={() => { setMode('login'); clearMessages(); }}>
                  ← Back to Sign In
                </button>
              </div>
            )}

            <button type="submit" className="cc-btn cc-btn-primary" disabled={submitting} style={{ marginBottom: '0' }}>
              {submitting ? <span className="cc-spinner" /> : (
                mode === 'login'  ? 'Sign In' :
                mode === 'signup' ? 'Create Account' :
                'Send Reset Link'
              )}
            </button>
          </form>

          {/* ── Google ── (not shown on reset) */}
          {mode !== 'reset' && (
            <>
              <div className="cc-divider">
                <div className="cc-divider-line" />
                <div className="cc-divider-text">or continue with</div>
                <div className="cc-divider-line" />
              </div>

              <button type="button" className="cc-btn cc-btn-google" onClick={handleGoogle} disabled={submitting}>
                <svg className="cc-google-icon" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                  <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
                  <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
                  <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
                  <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
                </svg>
                Continue with Google
              </button>
            </>
          )}

          <div className="cc-footer">
            Secured by Firebase Authentication
          </div>
        </div>
      </div>
    </>
  );
}
