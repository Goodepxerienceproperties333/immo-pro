import React from "react";

/**
 * iter90gz : ErrorBoundary global.
 *
 * Rattrape les crashs React (erreur pendant render, lifecycle, constructeur)
 * qui autrement produiraient un ECRAN BLANC total pour l'utilisateur.
 * A la place, on affiche un message clair avec deux actions : recharger la
 * page ou revenir au dashboard.
 *
 * Placer au-dessus de <AppRoutes /> dans App.js pour couvrir toute l'app.
 */
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, errorMessage: "", errorStack: "" };
  }

  static getDerivedStateFromError(error) {
    return {
      hasError: true,
      errorMessage: (error && error.message) || "Erreur inconnue",
      errorStack: (error && error.stack) || "",
    };
  }

  componentDidCatch(error, errorInfo) {
    // On log en console (visible dans le devtool). Le monitoring cote serveur
    // recevra les 5xx via /var/log/supervisor/backend_errors.log, mais les
    // crashs FRONT purs restent visibles seulement ici.
    console.error("[iter90gz ErrorBoundary]", error, errorInfo);
  }

  handleReload = () => {
    try {
      window.location.reload();
    } catch (_e) {
      // no-op
    }
  };

  handleHome = () => {
    try {
      window.location.href = "/";
    } catch (_e) {
      // no-op
    }
  };

  render() {
    if (!this.state.hasError) return this.props.children;
    return (
      <div
        data-testid="global-error-boundary"
        className="min-h-screen flex items-center justify-center bg-slate-50 p-4"
      >
        <div className="max-w-lg w-full bg-white border border-slate-200 rounded-lg shadow-sm p-8">
          <div className="flex items-center gap-3 mb-4">
            <div className="w-10 h-10 rounded-full bg-red-50 flex items-center justify-center">
              <svg
                xmlns="http://www.w3.org/2000/svg"
                width="20"
                height="20"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                className="text-red-600"
              >
                <circle cx="12" cy="12" r="10" />
                <line x1="12" y1="8" x2="12" y2="12" />
                <line x1="12" y1="16" x2="12.01" y2="16" />
              </svg>
            </div>
            <h1 className="text-lg font-semibold text-slate-900">
              Une erreur est survenue
            </h1>
          </div>
          <p className="text-sm text-slate-600 mb-2">
            L&apos;application a rencontre un probleme inattendu. Vos donnees sont
            sauvegardees. Vous pouvez recharger la page pour continuer.
          </p>
          <p className="text-xs text-slate-500 mb-6">
            Si le probleme persiste, contactez votre syndic ou l&apos;assistance
            technique en precisant l&apos;heure exacte de l&apos;incident.
          </p>
          <details className="mb-6 text-xs text-slate-500 bg-slate-50 rounded p-3 border border-slate-100">
            <summary className="cursor-pointer select-none">
              Details techniques
            </summary>
            <div
              className="mt-2 whitespace-pre-wrap break-words font-mono"
              data-testid="global-error-message"
            >
              {this.state.errorMessage}
            </div>
          </details>
          <div className="flex flex-col sm:flex-row gap-2">
            <button
              type="button"
              onClick={this.handleReload}
              data-testid="global-error-reload"
              className="flex-1 px-4 py-2 rounded bg-[#022D52] text-white text-sm font-medium hover:bg-[#043b6b] transition-colors"
            >
              Recharger la page
            </button>
            <button
              type="button"
              onClick={this.handleHome}
              data-testid="global-error-home"
              className="flex-1 px-4 py-2 rounded border border-slate-300 text-slate-700 text-sm font-medium hover:bg-slate-50 transition-colors"
            >
              Retour au tableau de bord
            </button>
          </div>
        </div>
      </div>
    );
  }
}
