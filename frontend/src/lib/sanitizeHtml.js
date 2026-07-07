/**
 * Helper XSS-safe pour rendre du HTML rich-text (templates emails,
 * signature MS Graph, etc.). Utilise DOMPurify (industrie standard).
 *
 * Bloque : <script>, on* handlers, iframe, object, embed, data: URLs
 * dans src/href, javascript:, etc. Preserve : formatting standard
 * (<p>, <br>, <strong>, <em>, <ul>, <li>, <a>, <img> avec http/https
 * uniquement, tables, styles inline autorises).
 *
 * Usage :
 *   import { sanitizeHtml } from '@/lib/sanitizeHtml';
 *   <div dangerouslySetInnerHTML={{ __html: sanitizeHtml(userContent) }} />
 */
import DOMPurify from 'dompurify';

const DEFAULT_CONFIG = {
  ALLOWED_TAGS: [
    'a', 'b', 'br', 'div', 'em', 'i', 'img', 'li', 'ol', 'p', 'span',
    'strong', 'table', 'tbody', 'td', 'tfoot', 'th', 'thead', 'tr', 'u',
    'ul', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'hr', 'pre',
    'code',
  ],
  ALLOWED_ATTR: ['href', 'target', 'rel', 'src', 'alt', 'title', 'style',
                 'class', 'width', 'height'],
  ALLOW_DATA_ATTR: false,
  FORBID_TAGS: ['script', 'style', 'iframe', 'object', 'embed', 'form',
                'input', 'button', 'link', 'meta'],
  FORBID_ATTR: ['onerror', 'onload', 'onclick', 'onmouseover', 'onfocus',
                'onblur', 'onchange', 'onsubmit'],
};

export function sanitizeHtml(html, config = {}) {
  if (!html) return '';
  return DOMPurify.sanitize(String(html), { ...DEFAULT_CONFIG, ...config });
}
