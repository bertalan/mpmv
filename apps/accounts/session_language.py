"""Where an anonymous visitor's language choice lives (I18N.11): the session, never a cookie.

The language switcher posts to `switch_language`; the prefix decides the language of public pages,
so an anonymous choice only matters on the unprefixed pages (sign-in, MFA steps), where
`PreferredLanguageMiddleware` reads it after a signed-in user's stored preference.
"""

SESSION_LANGUAGE_KEY = "mpmv_language"
