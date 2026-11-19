/**
 * Whether the signed-in user has hidden the Home page's "See what's possible" showcase
 * (`components/chat/Showcase.tsx`), and a setter that remembers the choice in this browser.
 *
 * Kept per browser and per user (localStorage is shared by everyone who uses the browser). The key is
 * the one the earlier "What I can help with" list used, so anyone who hid that stays hidden.
 */
import { useState } from 'react';

const storageKey = (user: string) => `landing_guide_hidden:${user}`;

function readHidden(user: string): boolean {
    try {
        return window.localStorage.getItem(storageKey(user)) === '1';
    } catch {
        return false;
    }
}

/**
 * Whether `user` has hidden the showcase, and a setter that remembers the choice in this browser.
 * Lives with the page rather than inside `Showcase` because the page shows its prompt suggestions only
 * while the showcase is hidden: the two would otherwise compete under the chat box.
 */
export function useShowcaseHidden(user: string): [boolean, (hidden: boolean) => void] {
    // What was clicked this session, for this user; otherwise the saved choice (the user isn't known on
    // the first render after a reload, so this can't be read once up front).
    const [clicked, setClicked] = useState<{ user: string; hidden: boolean } | null>(null);
    const hidden = clicked?.user === user ? clicked.hidden : readHidden(user);
    const setHidden = (value: boolean) => {
        setClicked({ user, hidden: value });
        try {
            if (value) window.localStorage.setItem(storageKey(user), '1');
            else window.localStorage.removeItem(storageKey(user));
        } catch {
            /* storage disabled: the choice just lasts until reload */
        }
    };
    return [hidden, setHidden];
}
