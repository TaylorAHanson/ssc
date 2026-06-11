import * as React from "react"
import { cn } from "../../lib/utils"

export interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  /**
   * Grow the textarea's height to fit its content as the user types,
   * up to the CSS `max-height` (which then takes over with a scrollbar).
   * Keeps a single-line input that expands into a multi-line composer.
   */
  autoResize?: boolean
}

const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, autoResize, value, ...props }, ref) => {
    const innerRef = React.useRef<HTMLTextAreaElement | null>(null)
    // Bridge the forwarded ref to our internal one so callers keep their handle
    // while we still own the node for measuring.
    React.useImperativeHandle(ref, () => innerRef.current as HTMLTextAreaElement)

    const resize = React.useCallback(() => {
      const el = innerRef.current
      if (!el || !autoResize) return
      // Reset first so the box can *shrink* when text is deleted, then grow to
      // fit. `max-height` (from className) clamps it and overflow scrolls.
      el.style.height = "auto"
      el.style.height = `${el.scrollHeight}px`
    }, [autoResize])

    // Recompute whenever the controlled value changes (covers programmatic
    // resets like clearing the input on submit).
    React.useLayoutEffect(() => {
      resize()
    }, [resize, value])

    return (
      <textarea
        className={cn(
          "flex min-h-[36px] w-full rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm ring-offset-white placeholder:text-gray-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 resize-none",
          autoResize && "overflow-y-auto",
          className
        )}
        ref={innerRef}
        value={value}
        onInput={autoResize ? resize : undefined}
        {...props}
      />
    )
  }
)
Textarea.displayName = "Textarea"

export { Textarea }

