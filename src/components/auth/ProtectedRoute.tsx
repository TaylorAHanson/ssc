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

    // Wait for hydration to avoid redirecting prematurely
    if (!hydrated) {
        return null; // Or a loading spinner
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
