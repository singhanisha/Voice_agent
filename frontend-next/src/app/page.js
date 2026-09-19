"use client";

import React, { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  ChevronDown, Phone, CheckCircle, Globe, MessageSquare,
  Zap, Shield, Settings, Play, ArrowUpRight
} from "lucide-react";
import Link from "next/link";

/* ─────────────────────────────────────────
   NAVBAR
───────────────────────────────────────── */
function Navbar() {
  return (
    <nav className="fixed top-6 inset-x-0 z-50 px-6 max-w-6xl mx-auto pointer-events-none">
      <div className="bg-brand-white border border-brand-border rounded-pill px-6 h-[72px] flex items-center justify-between shadow-sm pointer-events-auto">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-full bg-brand-black flex items-center justify-center">
            <div className="w-4 h-4 border-2 border-brand-white rounded-full border-t-transparent animate-spin" />
          </div>
          <span className="text-brand-black text-xl font-bold tracking-tight">Cosmic Chameleon</span>
        </div>

        <div className="hidden lg:flex items-center gap-9 font-mono text-xs font-medium uppercase tracking-widest text-brand-black">
          <Link href="#" className="hover:text-brand-muted transition-colors">Overview</Link>
          <Link href="#" className="hover:text-brand-muted transition-colors">Use Cases</Link>
          <Link href="#" className="hover:text-brand-muted transition-colors">Pricing</Link>
          <Link href="#" className="hover:text-brand-muted transition-colors">Developers</Link>
        </div>

        <div className="flex items-center gap-6">
          <Link href="/agents" className="hidden md:block font-mono text-xs uppercase tracking-widest text-brand-black hover:text-brand-muted transition-colors font-medium">
            Log In
          </Link>
          <button className="px-7 py-3 bg-brand-white border border-brand-border text-brand-black font-mono text-xs font-medium uppercase tracking-widest rounded-pill hover:bg-brand-cream transition-colors">
            Contact
          </button>
        </div>
      </div>
    </nav>
  );
}

/* ─────────────────────────────────────────
   HERO
───────────────────────────────────────── */
function Hero() {
  return (
    <section className="pt-[140px] border-b border-brand-border bg-brand-white">
      <div className="grid grid-cols-1 lg:grid-cols-[45%_55%] min-h-[650px]">
        {/* Text Column */}
        <div className="flex flex-col justify-center px-6 md:px-16 pb-20 pt-10 lg:border-r border-brand-border">
          <h1 className="text-[72px] font-semibold text-brand-black leading-[1.05] tracking-tight mb-8 max-w-[640px]">
            Build AI Voice Agents That Sound Human.
          </h1>
          <p className="text-[17px] text-brand-muted leading-relaxed font-normal mb-12 max-w-lg">
            Deploy next-generation voice agents that handle real conversations — appointments, sales,
            support — with sub-600ms response times and flawless barge-in handling.
          </p>
          
          <div className="flex flex-col sm:flex-row items-center gap-6">
            <Link
              href="/demo"
              className="px-7 py-4 bg-brand-black text-brand-white font-mono text-[13px] font-medium uppercase tracking-widest rounded-pill hover:opacity-80 transition-opacity flex items-center justify-center gap-2 w-full sm:w-auto"
            >
              Try Live Demo <ArrowUpRight size={16} />
            </Link>
            <button className="font-mono text-[13px] font-medium uppercase tracking-widest text-brand-black hover:text-brand-muted transition-colors flex items-center gap-2">
              See How <Play size={14} className="fill-brand-black" />
            </button>
          </div>
        </div>

        {/* Illustration Column */}
        <div className="bg-brand-white flex items-center justify-center p-10 relative overflow-hidden min-h-[400px]">
          <div className="absolute inset-0 opacity-10 bg-[url('https://www.transparenttextures.com/patterns/cubes.png')]" />
          <div className="relative z-10 w-full max-w-md h-[400px] border border-brand-black rounded-card bg-brand-cream overflow-hidden">
            <img src="/images/hero_etching.png" alt="Cosmic Chameleon Illustration" className="w-full h-full object-cover" />
          </div>
        </div>
      </div>
    </section>
  );
}

/* ─────────────────────────────────────────
   STATS UNDER HERO
───────────────────────────────────────── */
function StatsBar() {
  return (
    <section className="border-b border-brand-border bg-brand-white">
      <div className="grid grid-cols-1 lg:grid-cols-[45%_55%]">
        <div className="px-6 md:px-16 py-12 lg:border-r border-brand-border flex items-center border-b lg:border-b-0">
          <p className="text-[17px] text-brand-muted leading-relaxed font-normal">
            Trusted by fast-growing startups and enterprises to automate thousands of phone calls daily without sacrificing quality.
          </p>
        </div>
        <div className="px-6 md:px-16 py-12 grid grid-cols-3 gap-8 items-center">
          <div>
            <p className="font-mono text-[12px] text-brand-faint uppercase tracking-widest mb-2">Calls Handled</p>
            <p className="text-[38px] font-bold text-brand-black">2.4M+</p>
          </div>
          <div>
            <p className="font-mono text-[12px] text-brand-faint uppercase tracking-widest mb-2">Avg Latency</p>
            <p className="text-[38px] font-bold text-brand-black">{'<'}600ms</p>
          </div>
          <div>
            <p className="font-mono text-[12px] text-brand-faint uppercase tracking-widest mb-2">Uptime</p>
            <p className="text-[38px] font-bold text-brand-black">99.99%</p>
          </div>
        </div>
      </div>
    </section>
  );
}

/* ─────────────────────────────────────────
   FEATURES CREAM CARDS
───────────────────────────────────────── */
function Features() {
  const cells = [
    {
      title: "Sub-600ms Latency",
      desc: "Proprietary streaming pipeline eliminates awkward silences. Conversations feel instant.",
      label: "SPEED",
    },
    {
      title: "Real-Time Calling",
      desc: "Agents book, look up, and update records mid-sentence.",
      label: "ACTIONS",
    },
    {
      title: "Turn-Taking Model",
      desc: "Our VAD handles interruptions and barge-ins naturally.",
      label: "NLP",
    },
    {
      title: "Enterprise Security",
      desc: "SOC2 Type II, HIPAA-ready, GDPR compliant.",
      label: "COMPLIANCE",
    },
  ];

  return (
    <section className="py-[96px] px-6 bg-brand-white border-b border-brand-border">
      <div className="max-w-[1400px] mx-auto">
        <div className="mb-16 md:px-8">
          <h2 className="text-[48px] font-medium text-brand-black mb-4">Core platform capabilities</h2>
          <p className="text-[17px] text-brand-muted max-w-2xl">Every component engineered for naturalness at scale.</p>
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-5">
          {cells.map((c, i) => (
            <div key={i} className="bg-brand-cream rounded-card p-[36px] flex flex-col justify-between">
              <div>
                <p className="font-mono text-[12px] text-brand-faint uppercase tracking-widest mb-3">{c.label}</p>
                <h3 className="text-[28px] font-semibold text-brand-black leading-tight mb-4">{c.title}</h3>
                <p className="text-[17px] text-brand-muted leading-relaxed mb-6">{c.desc}</p>
              </div>
              <hr className="border-brand-border mb-5" />
              <button className="font-mono text-[13px] font-medium text-brand-black uppercase tracking-widest flex items-center gap-2 self-start hover:text-brand-muted transition-colors">
                Read Post <ArrowUpRight size={14} />
              </button>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ─────────────────────────────────────────
   CTA BANNER
───────────────────────────────────────── */
function CTABanner() {
  return (
    <section className="py-[96px] px-6 bg-brand-white border-b border-brand-border">
      <div className="max-w-[1400px] mx-auto bg-brand-black rounded-card p-[40px] py-[40px] md:py-[64px] px-[40px] md:px-[64px] flex flex-col md:flex-row items-center justify-between gap-8">
        <h2 className="text-[48px] font-medium text-brand-white leading-tight max-w-2xl">
          Ready to automate your calls?
        </h2>
        <button className="px-8 py-4 bg-brand-white text-brand-black font-mono text-[13px] font-medium uppercase tracking-widest rounded-pill hover:bg-brand-border transition-colors flex items-center gap-2 flex-shrink-0">
          Book Strategy Call <ArrowUpRight size={16} />
        </button>
      </div>
    </section>
  );
}

/* ─────────────────────────────────────────
   NUMBERED PROCESS
───────────────────────────────────────── */
function Process() {
  const steps = [
    {
      num: "01",
      title: "Define the objective",
      desc: "Set the goal for your agent—whether it's booking appointments, qualifying leads, or answering support questions."
    },
    {
      num: "02",
      title: "Connect your tools",
      desc: "Integrate with your CRM, calendar, and internal databases so the agent can take real actions during the call."
    },
    {
      num: "03",
      title: "Deploy and monitor",
      desc: "Launch your campaign and monitor real-time transcripts, analytics, and conversion rates directly from the dashboard."
    }
  ];

  return (
    <section className="py-[96px] px-6 bg-brand-white border-b border-brand-border">
      <div className="max-w-[1400px] mx-auto">
        <div className="mb-16 md:px-8">
          <h2 className="text-[48px] font-medium text-brand-black mb-4">How we work</h2>
          <p className="text-[17px] text-brand-muted max-w-2xl">Launch your AI workforce in three simple steps.</p>
        </div>
        
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {steps.map((step, i) => (
            <div key={i} className="bg-brand-cream border border-brand-border rounded-card p-[32px] flex flex-col sm:flex-row gap-6">
              <div className="w-[64px] flex-shrink-0">
                <span className="font-mono text-brand-muted text-[17px]">{step.num}</span>
              </div>
              <div>
                <h3 className="text-[24px] font-semibold text-brand-black mb-3">{step.title}</h3>
                <p className="text-[17px] text-brand-muted leading-relaxed">{step.desc}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ─────────────────────────────────────────
   FOOTER
───────────────────────────────────────── */
function Footer() {
  return (
    <footer className="bg-brand-white">
      <div className="max-w-[1400px] mx-auto px-6 py-[96px]">
        <div className="flex flex-col lg:flex-row gap-[48px] lg:gap-0">
          
          {/* Left Cream Card */}
          <div className="w-full lg:w-[42%] bg-brand-cream rounded-card p-[40px] flex flex-col items-start justify-between min-h-[320px]">
            <div>
              <div className="flex items-center gap-3 mb-6">
                <div className="w-8 h-8 rounded-full bg-brand-black flex items-center justify-center">
                  <div className="w-4 h-4 border-2 border-brand-white rounded-full border-t-transparent" />
                </div>
                <span className="text-brand-black text-xl font-bold tracking-tight">Cosmic Chameleon</span>
              </div>
              <p className="text-[28px] font-semibold text-brand-black leading-tight max-w-sm">
                Next-generation AI voice platform for enterprises.
              </p>
            </div>
            <button className="px-7 py-3 mt-8 bg-brand-black text-brand-white font-mono text-[13px] font-medium uppercase tracking-widest rounded-pill hover:opacity-80 transition-opacity flex items-center gap-2">
              Book Call <ArrowUpRight size={16} />
            </button>
          </div>

          {/* Right Link Directory */}
          <div className="w-full lg:w-[58%] lg:pl-[96px] pt-[20px]">
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-[48px]">
              
              <div className="flex flex-col gap-6">
                <h4 className="font-mono text-[12px] text-brand-faint uppercase tracking-widest">Company</h4>
                <div className="flex flex-col gap-4 font-mono text-[14px]">
                  <Link href="#" className="text-brand-black hover:text-brand-muted transition-colors">About</Link>
                  <Link href="#" className="text-brand-black hover:text-brand-muted transition-colors">Careers</Link>
                  <Link href="#" className="text-brand-black hover:text-brand-muted transition-colors">Blog</Link>
                  <Link href="#" className="text-brand-black hover:text-brand-muted transition-colors">Contact</Link>
                </div>
              </div>

              <div className="flex flex-col gap-6">
                <h4 className="font-mono text-[12px] text-brand-faint uppercase tracking-widest">Product</h4>
                <div className="flex flex-col gap-4 font-mono text-[14px]">
                  <Link href="#" className="text-brand-black hover:text-brand-muted transition-colors">Voice Agents</Link>
                  <Link href="#" className="text-brand-black hover:text-brand-muted transition-colors">Analytics</Link>
                  <Link href="#" className="text-brand-black hover:text-brand-muted transition-colors">Telephony</Link>
                  <Link href="#" className="text-brand-black hover:text-brand-muted transition-colors">Integrations</Link>
                </div>
              </div>

              <div className="flex flex-col gap-6">
                <h4 className="font-mono text-[12px] text-brand-faint uppercase tracking-widest">Support</h4>
                <div className="flex flex-col gap-4 font-mono text-[14px]">
                  <Link href="#" className="text-brand-black hover:text-brand-muted transition-colors">Documentation</Link>
                  <Link href="#" className="text-brand-black hover:text-brand-muted transition-colors">API Reference</Link>
                  <Link href="#" className="text-brand-black hover:text-brand-muted transition-colors">Status</Link>
                  <Link href="#" className="text-brand-black hover:text-brand-muted transition-colors">GitHub</Link>
                </div>
              </div>

            </div>
          </div>
        </div>

        <hr className="border-brand-border mt-[96px] mb-[32px]" />

        <div className="flex flex-col md:flex-row items-center justify-between gap-4 font-mono text-[12px] text-brand-muted">
          <p>Cosmic Chameleon, Inc. is a registered corporation.</p>
          <div className="flex flex-wrap gap-6">
            <Link href="#" className="hover:text-brand-black transition-colors">Privacy</Link>
            <Link href="#" className="hover:text-brand-black transition-colors">Terms</Link>
            <Link href="#" className="hover:text-brand-black transition-colors">Security</Link>
            <p>© 2026 Cosmic Chameleon, Inc.</p>
          </div>
        </div>
      </div>
    </footer>
  );
}

/* ─────────────────────────────────────────
   PAGE EXPORT
───────────────────────────────────────── */
export default function LandingPage() {
  return (
    <div className="bg-brand-white text-brand-black min-h-screen selection:bg-brand-black selection:text-brand-white font-sans">
      <Navbar />
      <main>
        <Hero />
        <StatsBar />
        <Process />
        <Features />
        <CTABanner />
      </main>
      <Footer />
    </div>
  );
}
