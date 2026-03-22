import isoCountries from 'i18n-iso-countries';
import en from 'i18n-iso-countries/langs/en.json';

isoCountries.registerLocale(en);

/** Sorted { code, name } for HTML selects (ISO 3166-1 alpha-2). */
export function getCountryOptions() {
  const names = isoCountries.getNames('en', { select: 'official' });
  return Object.entries(names)
    .map(([code, name]) => ({ code, name }))
    .sort((a, b) => a.name.localeCompare(b.name, 'en'));
}

/** Map saved settings (ISO2, legacy name, or "USA") to ISO2 for the dropdown. */
export function normalizeSavedCountryCode(saved) {
  const s = (saved || '').trim();
  if (!s) return '';
  if (s.length === 2 && /^[A-Za-z]{2}$/.test(s)) return s.toUpperCase();
  if (/^usa$/i.test(s)) return 'US';
  const code = isoCountries.getAlpha2Code(s, 'en');
  return code || '';
}
