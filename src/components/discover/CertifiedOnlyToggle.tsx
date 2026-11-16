import { ShieldCheck } from 'lucide-react';

interface CertifiedOnlyToggleProps {
  active: boolean;
  onToggle: () => void;
}

/** The "Certified Only" filter pill, shared by every Discover view that lists assets. */
export function CertifiedOnlyToggle({ active, onToggle }: CertifiedOnlyToggleProps) {
  return (
    <button
      type="button"
      onClick={onToggle}
      title="Show only certified assets"
      aria-pressed={active}
      className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold border transition-all cursor-pointer ${
        active
          ? 'bg-emerald-50 text-emerald-800 border-emerald-300 shadow-2xs font-bold'
          : 'bg-white text-slate-600 border-slate-200 hover:bg-slate-50'
      }`}
    >
      <ShieldCheck className="w-3.5 h-3.5 text-emerald-600" />
      <span className="hidden 2xl:inline">Certified Only</span>
    </button>
  );
}
