import { Container, getContainer } from "@cloudflare/containers";
import { env } from "cloudflare:workers";

function configuredContainerEnv() {
  const values = {
    DJANGO_DEBUG: "false",
    DJANGO_SECRET_KEY: env.DJANGO_SECRET_KEY,
    DJANGO_ALLOWED_HOSTS:
      env.DJANGO_ALLOWED_HOSTS ?? ".workers.dev,corect.uk,www.corect.uk",
    DJANGO_CSRF_TRUSTED_ORIGINS:
      env.DJANGO_CSRF_TRUSTED_ORIGINS ??
      "https://*.workers.dev,https://corect.uk,https://www.corect.uk",

    DATABASE_NAME: env.DATABASE_NAME,
    DATABASE_USER: env.DATABASE_USER,
    DATABASE_PASSWORD: env.DATABASE_PASSWORD,
    DATABASE_HOST: env.DATABASE_HOST,
    DATABASE_PORT: env.DATABASE_PORT,

    OPENAI_API_KEY: env.OPENAI_API_KEY,
    OPENAI_MODEL: env.OPENAI_MODEL,

    CONTACT_EMAIL: env.CONTACT_EMAIL,
    LEGAL_OPERATOR_TYPE: env.LEGAL_OPERATOR_TYPE,
    LEGAL_OPERATOR_NAME: env.LEGAL_OPERATOR_NAME,
    LEGAL_TRADING_NAME: env.LEGAL_TRADING_NAME ?? "Corect.uk",
    LEGAL_SERVICE_ADDRESS: env.LEGAL_SERVICE_ADDRESS,
    LEGAL_JURISDICTION: env.LEGAL_JURISDICTION ?? "England and Wales",
    LEGAL_COUNTRY: env.LEGAL_COUNTRY ?? "United Kingdom",
    COMPANY_NUMBER: env.COMPANY_NUMBER,
    VAT_NUMBER: env.VAT_NUMBER,
    LEGAL_HOSTING_PROVIDER: env.LEGAL_HOSTING_PROVIDER ?? "Cloudflare"
  };

  return Object.fromEntries(
    Object.entries(values).filter(
      ([, value]) => typeof value === "string" && value.length > 0
    )
  );
}

export class CorectContainer extends Container {
  defaultPort = 8080;
  sleepAfter = "10m";
  enableInternet = true;
  envVars = configuredContainerEnv();
}

export default {
  async fetch(request, bindings) {
    return getContainer(bindings.CORECT_CONTAINER).fetch(request);
  }
};
