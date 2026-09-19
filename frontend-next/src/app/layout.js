import 'bootstrap/dist/css/bootstrap.min.css';
import "./globals.css";
import { AuthProvider } from '../context/AuthContext';
import { Outfit, Space_Mono } from 'next/font/google';

const outfit = Outfit({ 
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-sans',
});

const spaceMono = Space_Mono({ 
  weight: ['400', '700'],
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-mono',
});

export const metadata = {
  title: "Cosmic Chameleon | Voice Agent Platform",
  description: "AI-Powered Real Estate Voice Agent Platform",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" data-scroll-behavior="smooth" className={`${outfit.variable} ${spaceMono.variable}`}>
      <body suppressHydrationWarning>
        <AuthProvider>
          {children}
        </AuthProvider>
      </body>
    </html>
  );
}
