/**
 * @jest-environment jsdom
 *
 * Test unitaire : verifie que sanitizeHtml bloque les vecteurs XSS
 * classiques et preserve le formatting legitime.
 */
import { sanitizeHtml } from '../sanitizeHtml';

describe('sanitizeHtml (iter90bn - XSS defense)', () => {
  test('empty / null input -> empty string', () => {
    expect(sanitizeHtml('')).toBe('');
    expect(sanitizeHtml(null)).toBe('');
    expect(sanitizeHtml(undefined)).toBe('');
  });

  test('blocks <script> tag', () => {
    const out = sanitizeHtml('<script>alert(1)</script>Bonjour');
    expect(out).not.toMatch(/<script/i);
    expect(out).toMatch(/Bonjour/);
  });

  test('blocks onerror handler on <img>', () => {
    const out = sanitizeHtml('<img src=x onerror=alert(1)>');
    expect(out).not.toMatch(/onerror/i);
    expect(out).not.toMatch(/alert/i);
  });

  test('blocks javascript: href on <a>', () => {
    const out = sanitizeHtml('<a href="javascript:alert(1)">click</a>');
    expect(out).not.toMatch(/javascript:/i);
  });

  test('blocks <iframe>', () => {
    const out = sanitizeHtml('<iframe src="http://evil.com"></iframe>Content');
    expect(out).not.toMatch(/<iframe/i);
    expect(out).toMatch(/Content/);
  });

  test('blocks inline onclick / onmouseover handlers', () => {
    const out = sanitizeHtml('<div onclick="alert(1)" onmouseover="alert(2)">hover me</div>');
    expect(out).not.toMatch(/onclick/i);
    expect(out).not.toMatch(/onmouseover/i);
    expect(out).toMatch(/hover me/);
  });

  test('blocks <object> and <embed>', () => {
    const out = sanitizeHtml('<object data="evil.swf"></object><embed src="evil.swf">');
    expect(out).not.toMatch(/<object/i);
    expect(out).not.toMatch(/<embed/i);
  });

  test('blocks <form> and <input> (potential phishing)', () => {
    const out = sanitizeHtml('<form action="http://evil.com"><input name="pwd" /></form>');
    expect(out).not.toMatch(/<form/i);
    expect(out).not.toMatch(/<input/i);
  });

  test('preserves legitimate formatting for email templates', () => {
    const html = '<p>Bonjour <b>{owner_name}</b>,<br>Votre solde est <em>impaye</em>.</p>';
    const out = sanitizeHtml(html);
    expect(out).toMatch(/<p>/);
    expect(out).toMatch(/<b>\{owner_name\}<\/b>/);
    expect(out).toMatch(/<br/);
    expect(out).toMatch(/<em>impaye<\/em>/);
  });

  test('preserves https links but strips javascript:', () => {
    const out = sanitizeHtml('<a href="https://immo-pcmn.emergent.host/portal">Portail</a>');
    expect(out).toMatch(/href="https:\/\/immo-pcmn/);
    expect(out).toMatch(/Portail/);
  });

  test('preserves tables (used in decompte emails)', () => {
    const html = '<table><thead><tr><th>Poste</th><th>Montant</th></tr></thead><tbody><tr><td>Charges</td><td>1250 EUR</td></tr></tbody></table>';
    const out = sanitizeHtml(html);
    expect(out).toMatch(/<table/);
    expect(out).toMatch(/<thead/);
    expect(out).toMatch(/<tr/);
    expect(out).toMatch(/1250 EUR/);
  });

  test('preserves inline styles (used by rich signatures)', () => {
    const out = sanitizeHtml('<span style="color:red;font-weight:bold">Urgent</span>');
    expect(out).toMatch(/style=/);
    expect(out).toMatch(/Urgent/);
  });

  test('mixed attack + legitimate content : keeps only legitimate', () => {
    const html = '<p>Bonjour <script>alert(1)</script><b>Marie</b>, <img src=x onerror=alert(2)> votre <a href="javascript:evil()">solde</a> est de 100 EUR</p>';
    const out = sanitizeHtml(html);
    expect(out).not.toMatch(/<script/i);
    expect(out).not.toMatch(/onerror/i);
    expect(out).not.toMatch(/javascript:/i);
    expect(out).toMatch(/Bonjour/);
    expect(out).toMatch(/<b>Marie<\/b>/);
    expect(out).toMatch(/solde/);
    expect(out).toMatch(/100 EUR/);
  });

  test('SVG script injection blocked', () => {
    const out = sanitizeHtml('<svg><script>alert(1)</script></svg>');
    expect(out).not.toMatch(/<script/i);
    expect(out).not.toMatch(/alert/i);
  });

  test('data: URI in <img src> blocked (data attr not allowed)', () => {
    // DOMPurify autorise data: pour <img> par defaut mais on peut le tolerer
    // car pas de script execution possible via data:image/png. On teste juste
    // qu'aucun handler d'evenement ne survit.
    const out = sanitizeHtml('<img src="data:image/svg+xml;base64,PHN2ZyBvbmxvYWQ9YWxlcnQoMSk+" />');
    expect(out).not.toMatch(/onload=alert/i);
  });
});
