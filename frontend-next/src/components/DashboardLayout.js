'use client';
import { useAuth, clientProfile } from '../context/AuthContext';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useRouter } from 'next/navigation';
import { useEffect } from 'react';
import { 
  Activity, Rocket, Bot, BrainCircuit, Building2, ClipboardEdit, ShieldCheck, Phone, PhoneCall,
  Home, PlaySquare, BarChart3, LogOut 
} from 'lucide-react';

const CRM_READINESS_UI_ENABLED = process.env.NEXT_PUBLIC_CRM_READINESS_UI_ENABLED === 'true';
const SCRAPE_GENERATE_SCRIPT_ENABLED = process.env.NEXT_PUBLIC_SCRAPE_GENERATE_SCRIPT_ENABLED === 'true';

export default function DashboardLayout({ children }) {
  const { currentRole, activeClient, setActiveClient, logout, user, loading } = useAuth();
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !currentRole) router.push('/login');
  }, [loading, currentRole, router]);

  if (loading || !currentRole || !user) return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', background: 'var(--color-white)' }}>
      <div className="cc-spinner" />
    </div>
  );

  const adminMenu = [
    { label: 'Live Monitor', path: '/monitor', icon: <Activity size={18} strokeWidth={1.5} /> },
    { label: 'Campaigns', path: '/campaigns', icon: <Rocket size={18} strokeWidth={1.5} /> },
    { label: 'Voice Agents', path: '/agents', icon: <Bot size={18} strokeWidth={1.5} /> },
    ...(SCRAPE_GENERATE_SCRIPT_ENABLED ? [{ label: 'Intelligence', path: '/intelligence', icon: <BrainCircuit size={18} strokeWidth={1.5} /> }] : []),
    { label: 'Clients', path: '/clients', icon: <Building2 size={18} strokeWidth={1.5} /> },
    { label: 'Demo Requests', path: '/demo-requests', icon: <ClipboardEdit size={18} strokeWidth={1.5} /> },
    ...(CRM_READINESS_UI_ENABLED ? [{ label: 'CRM Readiness', path: '/crm-readiness', icon: <ShieldCheck size={18} strokeWidth={1.5} /> }] : []),
    { label: 'Telephony', path: '/numbers', icon: <Phone size={18} strokeWidth={1.5} /> },
    { label: 'Call Logs & QA', path: '/logs', icon: <PhoneCall size={18} strokeWidth={1.5} /> }
  ];

  const clientMenu = [
    { label: 'Dashboard', path: '/client-dashboard', icon: <Home size={18} strokeWidth={1.5} /> },
    { label: 'Demo Campaign', path: '/demo', icon: <PlaySquare size={18} strokeWidth={1.5} /> },
    { label: 'Call Results', path: '/results', icon: <BarChart3 size={18} strokeWidth={1.5} /> }
  ];

  const menu = currentRole === 'admin' ? adminMenu : clientMenu;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: 'var(--color-white)', color: 'var(--color-black)', fontFamily: "var(--font-sans, sans-serif)" }}>
      
      {/* ── Header ── */}
      <header style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        padding: '0 24px', height: '56px',
        background: 'var(--color-white)',
        borderBottom: '1px solid var(--color-border)',
        flexShrink: 0,
        zIndex: 10
      }}>
        {/* Logo */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <span style={{ fontSize: '16px', fontWeight: 600, letterSpacing: '-0.2px', color: 'var(--color-black)' }}>
            Cosmic <span style={{ color: 'var(--color-text-muted)', fontWeight: 500 }}>Chameleon</span>
          </span>
          {/* Version badge — outline only, no fill */}
          <span style={{
            padding: '2px 8px',
            border: '1px solid var(--color-border)',
            borderRadius: '999px',
            fontSize: '11px',
            color: 'var(--color-text-faint)',
            letterSpacing: '0.04em',
            fontFamily: 'var(--font-mono, monospace)'
          }}>
            v2.0 Beta
          </span>
        </div>

        {/* Right side controls */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          {currentRole === 'admin' && (
            <select
              style={{
                background: 'var(--color-white)',
                border: '1px solid var(--color-border)',
                color: 'var(--color-black)',
                fontSize: '13px',
                borderRadius: '6px',
                padding: '6px 12px',
                outline: 'none',
                width: '192px',
                cursor: 'pointer'
              }}
              value={activeClient}
              onChange={(e) => setActiveClient(e.target.value)}
            >
              {Object.keys(clientProfile).map(key => (
                <option key={key} value={key}>Viewing: {clientProfile[key].name}</option>
              ))}
            </select>
          )}

          {/* User profile — plain circle, no colored ring */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            {user.photoURL ? (
              <img
                src={user.photoURL}
                alt={user.name}
                referrerPolicy="no-referrer"
                style={{ width: '32px', height: '32px', borderRadius: '50%', objectFit: 'cover', border: '1px solid var(--color-border)' }}
              />
            ) : (
              <div style={{
                width: '32px', height: '32px', borderRadius: '50%',
                background: 'var(--color-cream)', border: '1px solid var(--color-border)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: '12px', fontWeight: 600, color: 'var(--color-black)'
              }}>
                {user.initials}
              </div>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.2 }}>
              <span style={{ fontSize: '13px', fontWeight: 500, color: 'var(--color-black)' }}>{user.name}</span>
              <span style={{ fontSize: '11px', color: 'var(--color-text-faint)', textTransform: 'capitalize' }}>{currentRole} Access</span>
            </div>
          </div>

          <button
            onClick={logout}
            style={{
              display: 'flex', alignItems: 'center', gap: '6px',
              padding: '6px 10px',
              background: 'transparent',
              border: 'none',
              color: 'var(--color-text-faint)',
              fontSize: '13px',
              borderRadius: '6px',
              cursor: 'pointer',
              transition: 'color 100ms ease-out, background-color 100ms ease-out'
            }}
            onMouseEnter={e => { e.currentTarget.style.color = 'var(--color-black)'; e.currentTarget.style.background = 'var(--color-cream)'; }}
            onMouseLeave={e => { e.currentTarget.style.color = 'var(--color-text-faint)'; e.currentTarget.style.background = 'transparent'; }}
          >
            <LogOut size={15} strokeWidth={1.5} /> Sign Out
          </button>
        </div>
      </header>

      {/* ── Main layout ── */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        
        {/* ── Sidebar ── */}
        <aside style={{
          background: 'var(--color-white)',
          borderRight: '1px solid var(--color-border)',
          display: 'flex',
          flexDirection: 'column',
          width: '220px',
          flexShrink: 0
        }}>
          {/* Section label */}
          <div style={{
            padding: '20px 16px 8px',
            fontSize: '11px',
            fontWeight: 600,
            color: 'var(--color-text-faint)',
            textTransform: 'uppercase',
            letterSpacing: '0.1em',
            fontFamily: 'var(--font-mono, monospace)'
          }}>
            Main Menu
          </div>

          <nav style={{ flex: 1, padding: '4px 8px', overflowY: 'auto' }}>
            {menu.map(item => {
              const isActive = pathname === item.path;
              return (
                <Link
                  key={item.path}
                  href={item.path}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '10px',
                    padding: '8px 10px',
                    marginBottom: '2px',
                    borderRadius: '6px',
                    textDecoration: 'none',
                    fontSize: '13px',
                    fontWeight: isActive ? 500 : 400,
                    color: isActive ? 'var(--color-black)' : 'var(--color-text-muted)',
                    background: isActive ? 'var(--color-cream)' : 'transparent',
                    /* Left-border indicator for active item */
                    borderLeft: isActive ? '2px solid var(--color-black)' : '2px solid transparent',
                    paddingLeft: isActive ? '9px' : '9px',
                    transition: 'background-color 100ms ease-out, color 100ms ease-out'
                  }}
                  onMouseEnter={e => {
                    if (!isActive) {
                      e.currentTarget.style.background = 'var(--color-cream)';
                      e.currentTarget.style.color = 'var(--color-black)';
                    }
                  }}
                  onMouseLeave={e => {
                    if (!isActive) {
                      e.currentTarget.style.background = 'transparent';
                      e.currentTarget.style.color = 'var(--color-text-muted)';
                    }
                  }}
                >
                  <span style={{ color: isActive ? 'var(--color-black)' : 'var(--color-text-faint)', display: 'flex', alignItems: 'center' }}>
                    {item.icon}
                  </span>
                  {item.label}
                </Link>
              );
            })}
          </nav>
        </aside>

        {/* ── Content area ── */}
        <main style={{ flex: 1, padding: '32px', overflowY: 'auto', background: 'var(--color-white)' }}>
          <div style={{ maxWidth: '1152px', margin: '0 auto' }}>
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
