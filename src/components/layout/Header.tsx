import { Bell } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useRequestStore } from '../../stores/requestStore';

export function Header() {
  const bannerMessage = useRequestStore((state) => state.bannerMessage);
  const pendingCount = useRequestStore((state) => state.getPendingApprovalsCount());

  return (
    <header className="bg-white border-b border-gray-200">
      {bannerMessage && (
        <div className="bg-yellow-50 border-b border-yellow-200 px-6 py-2">
          <p className="text-sm text-yellow-800">{bannerMessage}</p>
        </div>
      )}
      <div className="flex items-center justify-end px-6 py-4">
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

