import { useState } from 'react';
import { Bell, X } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useRequestStore } from '../../stores/requestStore';

export function Header() {
  const pendingCount = useRequestStore((state) => state.getPendingApprovalsCount());
  const bannerData = useRequestStore((state) => state.bannerData);
  const [isDismissed, setIsDismissed] = useState(false);

  const getBannerStyles = (type?: string) => {
    switch (type) {
      case 'alert':
        return 'bg-red-50 border border-red-200 text-red-800';
      case 'warning':
        return 'bg-yellow-50 border border-yellow-200 text-yellow-800';
      case 'success':
        return 'bg-green-50 border border-green-200 text-green-800';
      case 'info':
      default:
        return 'bg-blue-50 border border-blue-200 text-blue-800';
    }
  };

  const showBanner = bannerData && bannerData.active && bannerData.message && !isDismissed;

  return (
    <header className="bg-white border-b border-gray-200">
      <div className="flex items-center justify-between px-6 py-4">
        {showBanner && (
          <div className={`${getBannerStyles(bannerData.type)} px-4 py-2 rounded-md flex items-center gap-3 flex-1 max-w-2xl`}>
            <p className="text-sm flex-1">{bannerData.message}</p>
            <button
              onClick={() => setIsDismissed(true)}
              className="hover:opacity-70 transition-opacity flex-shrink-0"
              aria-label="Dismiss banner"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        )}
        {!showBanner && <div className="flex-1" />}
        <div className="flex items-center gap-4">
          <Link
            to="/admin/approvals"
            className="relative p-2 rounded-md hover:bg-gray-100 cursor-pointer transition-colors"
          >
            <Bell className="w-5 h-5 text-gray-600" />
            {pendingCount > 0 && (
              <span className="absolute -top-1 -right-1 w-5 h-5 bg-red-500 text-white text-xs font-bold rounded-full flex items-center justify-center">
                {pendingCount > 9 ? '9+' : pendingCount}
              </span>
            )}
          </Link>
          <div className="flex items-center gap-3 px-3 py-2 rounded-md hover:bg-gray-100 cursor-pointer transition-colors">
            <div className="w-8 h-8 rounded-full bg-primary text-white flex items-center justify-center text-sm font-semibold">
              TH
            </div>
            <div className="flex flex-col">
              <span className="text-sm font-medium text-gray-700">User Profile</span>
              <span className="text-xs text-gray-500">taylor.hanson@qualcomm.com</span>
            </div>
          </div>
        </div>
      </div>
    </header>
  );
}

