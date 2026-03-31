import { Github } from "lucide-react";

interface FooterColumn {
  title: string;
  links: FooterLink[];
}

interface FooterLink {
  label: string;
  href: string;
  external?: boolean;
}

const COLUMNS: FooterColumn[] = [
  {
    title: "Product",
    links: [
      { label: "How It Compares", href: "#comparison" },
      { label: "Ecosystem", href: "#ecosystem" },
      { label: "The Problem", href: "#problem" },
      { label: "Stats", href: "#stats" },
    ],
  },
  {
    title: "Install",
    links: [
      {
        label: "PyPI",
        href: "https://pypi.org/project/nerva/",
        external: true,
      },
      { label: "npm", href: "https://www.npmjs.com/package/nerva", external: true },
      { label: "Go", href: "https://pkg.go.dev/github.com/otomus/nerva", external: true },
      { label: "Rust", href: "https://crates.io/crates/nerva", external: true },
    ],
  },
  {
    title: "Resources",
    links: [
      { label: "Documentation", href: "/nerva/docs/" },
      {
        label: "GitHub",
        href: "https://github.com/otomus/nerva",
        external: true,
      },
      {
        label: "Contributing",
        href: "https://github.com/otomus/nerva/blob/main/CONTRIBUTING.md",
        external: true,
      },
      {
        label: "Examples",
        href: "https://github.com/otomus/nerva/tree/main/examples",
        external: true,
      },
    ],
  },
  {
    title: "Community",
    links: [
      {
        label: "GitHub Issues",
        href: "https://github.com/otomus/nerva/issues",
        external: true,
      },
      {
        label: "Discussions",
        href: "https://github.com/otomus/nerva/discussions",
        external: true,
      },
    ],
  },
];

const CURRENT_YEAR = new Date().getFullYear();

/**
 * Site footer with four link columns and a bottom bar
 * containing the logo wordmark and copyright notice.
 */
export function Footer(): JSX.Element {
  return (
    <footer className="bg-[#030710] px-6 pb-10 pt-16">
      <div className="gradient-line mx-auto mb-12 max-w-6xl" />

      <div className="mx-auto max-w-6xl">
        <div className="grid grid-cols-2 gap-10 md:grid-cols-4">
          {COLUMNS.map((column) => (
            <FooterColumnBlock key={column.title} column={column} />
          ))}
        </div>

        <BottomBar />
      </div>
    </footer>
  );
}

/** Single footer column with title and link list. */
function FooterColumnBlock({ column }: { column: FooterColumn }): JSX.Element {
  return (
    <div>
      <h4
        className="mb-4 text-sm font-semibold uppercase tracking-wider text-white"
        style={{ fontFamily: "'JetBrains Mono', monospace" }}
      >
        {column.title}
      </h4>

      <ul className="space-y-2">
        {column.links.map((link) => (
          <li key={link.label}>
            <a
              href={link.href}
              className="text-sm text-white/60 transition-colors hover:text-[#7FC8FF]"
              {...(link.external
                ? { target: "_blank", rel: "noopener noreferrer" }
                : {})}
            >
              {link.label}
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Bottom row with logo, tagline, and copyright. */
function BottomBar(): JSX.Element {
  return (
    <div className="mt-16 flex flex-col items-center justify-between gap-4 border-t border-white/5 pt-8 md:flex-row">
      <div className="flex items-center gap-3">
        <span
          className="text-lg font-bold text-white"
          style={{ fontFamily: "'JetBrains Mono', monospace" }}
        >
          nerva
        </span>
        <span className="text-sm text-white/40">
          Composable Agent Primitives
        </span>
      </div>

      <div className="flex items-center gap-4">
        <a
          href="https://github.com/otomus/nerva"
          target="_blank"
          rel="noopener noreferrer"
          className="text-white/40 transition-colors hover:text-[#7FC8FF]"
          aria-label="GitHub"
        >
          <Github size={18} />
        </a>
        <span className="text-sm text-white/40">
          &copy; {CURRENT_YEAR} nerva. All rights reserved.
        </span>
      </div>
    </div>
  );
}
