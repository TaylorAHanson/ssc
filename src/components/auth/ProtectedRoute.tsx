import { Navigate } from 'react-router-dom';
import { useUserStore } from '../../stores/userStore';
import type { UserPersona } from '../../types';

interface ProtectedRouteProps {
    children: React.ReactNode;
    allowedPersonas: UserPersona[];
}

export function ProtectedRoute({ children, allowedPersonas }: ProtectedRouteProps) {
    const currentPersona = useUserStore((state) => state.currentPersona);
    const hydrated = useUserStore((state) => state.hydrated);
    const isLoading = useUserStore((state) => state.isLoading);
    const isInitialized = useUserStore((state) => state.isInitialized);
    const error = useUserStore((state) => state.error);

    // Wait for hydration and initialization to avoid redirecting prematurely
    if (!hydrated || (!isInitialized && !error)) {
        return null; // Or a loading spinner
    }

    // If there's an error but we're not initialized, we shouldn't redirect to home
    // because we don't know the user's role yet. Stay on page (blank or error message)
    if (error && !isInitialized) {
        return (
            <div className="flex items-center justify-center min-h-screen">
                <div className="text-center p-8 bg-red-50 text-red-700 rounded-lg border border-red-200">
                    <h2 className="text-xl font-bold mb-2">Connection Error</h2>
                    <p>{error}</p>
                    <button
                        onClick={() => window.location.reload()}
                        className="mt-4 px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700 transition"
                    >
                        Retry
                    </button>
                </div>
            </div>
        );
    }

    if (isLoading && !currentPersona) {
        return null;
    }

    if (!allowedPersonas.includes(currentPersona)) {
        console.warn(`Access denied for persona: ${currentPersona}. Required: ${allowedPersonas.join(', ')}`);
        return <Navigate to="/" replace />;
    }

    return <>{children}</>;
}
