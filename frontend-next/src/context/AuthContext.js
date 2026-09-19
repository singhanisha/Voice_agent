'use client';
import { createContext, useContext, useState, useEffect } from 'react';
import { auth } from '../lib/firebase';
import {
  onAuthStateChanged,
  signOut,
} from 'firebase/auth';

// ─── ADMIN EMAILS ─────────────────────────────────────────────────────────────
// Add any email that should have admin access here.
const ADMIN_EMAILS = ['navneet@jobjockey.in', 'vishnu@jobjockey.in', 'parth@jobjockey.in', 'maniarasan@jobjockey.in', 'roy2272234@gmail.com','anishas2218@gmail.com'];

// ─── CLIENT PROFILES (unchanged) ──────────────────────────────────────────────
export const clientProfile = {
  'realty-demo': { name: 'Realty Pro', agent: 'Neha', agentId: 'real-estate-demo', role: 'client', initials: 'RP' },
  'finserv': { name: 'FinServ Plus', agent: 'Arjun', agentId: 'insurance-renewal', role: 'client', initials: 'FS' },
};

// ─── ADMIN USER SHAPE (for DashboardLayout compatibility) ─────────────────────
export const adminUser = { name: 'Platform Admin', email: '', role: 'admin', initials: 'PA' };

// ─── HELPERS ──────────────────────────────────────────────────────────────────
function deriveRole(firebaseUser) {
  if (!firebaseUser) return null;
  return ADMIN_EMAILS.includes(firebaseUser.email?.toLowerCase()) ? 'admin' : 'client';
}

function getInitials(displayName, email) {
  if (displayName) {
    const parts = displayName.trim().split(' ');
    return parts.length >= 2
      ? (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
      : displayName.slice(0, 2).toUpperCase();
  }
  return email ? email.slice(0, 2).toUpperCase() : '??';
}

// ─── CONTEXT ──────────────────────────────────────────────────────────────────
const AuthContext = createContext(null);

export const AuthProvider = ({ children }) => {
  const [currentRole, setCurrentRole] = useState(null);   // 'admin' | 'client' | null
  const [firebaseUser, setFirebaseUser] = useState(null);
  const [activeClient, setActiveClient] = useState('');
  const [clientAssignment, setClientAssignment] = useState(null);
  const [loading, setLoading] = useState(true);            // prevents flash of login page
  const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

  useEffect(() => {
    const unsub = onAuthStateChanged(auth, (user) => {
      setFirebaseUser(user);
      setCurrentRole(user ? deriveRole(user) : null);
      setLoading(false);
    });
    return unsub;
  }, []);

  useEffect(() => {
    let cancelled = false;
    const email = firebaseUser?.email?.toLowerCase();

    if (!email || currentRole !== 'client') {
      Promise.resolve().then(() => {
        if (!cancelled) setClientAssignment(null);
      });
      return () => {
        cancelled = true;
      };
    }

    fetch(`${API}/api/clients/resolve?email=${encodeURIComponent(email)}`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (cancelled) return;
        setClientAssignment(data);
        if (data?.client?.id) setActiveClient(data.client.id);
      })
      .catch(() => {
        if (!cancelled) setClientAssignment(null);
      });

    return () => {
      cancelled = true;
    };
  }, [API, currentRole, firebaseUser]);

  // Kept for backward-compat (DashboardLayout calls logout())
  const logout = async () => {
    await signOut(auth);
    setCurrentRole(null);
    setFirebaseUser(null);
  };

  // Kept for backward-compat (page.js used to call login(role) directly)
  // Now it's a no-op because Firebase onAuthStateChanged handles state.
  const login = () => { };

  // Build the user shape that DashboardLayout expects
  const user = firebaseUser
    ? {
      name: firebaseUser.displayName || firebaseUser.email?.split('@')[0] || 'User',
      email: firebaseUser.email,
      role: currentRole,
      initials: getInitials(firebaseUser.displayName, firebaseUser.email),
      photoURL: firebaseUser.photoURL,
      clientId: clientAssignment?.client?.id || null,
      clientName: clientAssignment?.client?.name || null,
      agentId: clientAssignment?.assignment?.agentId || null,
      agentName: clientAssignment?.assignment?.agent?.name || null,
    }
    : null;

  return (
    <AuthContext.Provider
      value={{
        currentRole,
        activeClient,
        setActiveClient,
        login,
        logout,
        firebaseUser,
        clientAssignment,
        user,
        loading,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => useContext(AuthContext);
