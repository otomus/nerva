import { useState } from "react";
import { Link } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import { Menu, X } from "lucide-react";

interface NavItem {
  readonly label: string;
  readonly href: string;
  readonly external?: boolean;
}

const NAV_LINKS: readonly NavItem[] = [
  { label: "Docs", href: "/nerva/docs/" },
  { label: "GitHub", href: "https://github.com/otomus/nerva", external: true },
  { label: "PyPI", href: "https://pypi.org/project/nerva/", external: true },
  { label: "npm", href: "https://www.npmjs.com/package/nerva", external: true },
];

/**
 * Sticky top navigation bar — logo, external links, and Get Started CTA.
 * All technical pages live in Docs; the landing page is marketing-only.
 */
export function Navbar() {
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);

  return (
    <nav className="fixed top-0 left-0 right-0 z-50 bg-[#030710]/80 backdrop-blur-lg border-b border-white/5">
      <div className="mx-auto max-w-7xl px-6 py-4 flex items-center justify-between">
        <Link to="/" className="text-[#7FC8FF] font-bold text-xl font-['JetBrains_Mono']">
          nerva
        </Link>

        <div className="hidden md:flex items-center gap-6">
          {NAV_LINKS.map((link) => (
            <a
              key={link.label}
              href={link.href}
              {...(link.external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
              className="text-sm text-gray-400 hover:text-white transition-colors"
            >
              {link.label}
            </a>
          ))}
          <a
            href="/nerva/docs/"
            className="ml-2 px-4 py-2 rounded-lg bg-[#7FC8FF] text-[#030710] text-sm font-semibold hover:bg-[#7FC8FF]/90 transition-colors"
          >
            Get Started
          </a>
        </div>

        <button
          onClick={() => setIsMobileMenuOpen((prev) => !prev)}
          className="md:hidden text-gray-400 hover:text-white transition-colors"
          aria-label={isMobileMenuOpen ? "Close menu" : "Open menu"}
        >
          {isMobileMenuOpen ? <X size={24} /> : <Menu size={24} />}
        </button>
      </div>

      <AnimatePresence>
        {isMobileMenuOpen && (
          <MobileMenu onClose={() => setIsMobileMenuOpen(false)} />
        )}
      </AnimatePresence>
    </nav>
  );
}

function MobileMenu({ onClose }: { onClose: () => void }) {
  return (
    <motion.div
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: "auto" }}
      exit={{ opacity: 0, height: 0 }}
      className="md:hidden overflow-hidden bg-[#030710]/95 backdrop-blur-lg border-b border-white/5"
    >
      <div className="flex flex-col gap-4 px-6 py-6">
        {NAV_LINKS.map((link) => (
          <a
            key={link.label}
            href={link.href}
            {...(link.external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
            onClick={onClose}
            className="text-sm text-gray-400 hover:text-white transition-colors"
          >
            {link.label}
          </a>
        ))}
        <a
          href="/nerva/docs/"
          onClick={onClose}
          className="mt-2 inline-block text-center px-4 py-2 rounded-lg bg-[#7FC8FF] text-[#030710] text-sm font-semibold"
        >
          Get Started
        </a>
      </div>
    </motion.div>
  );
}
