---
name: agentgateway-log-analytics
description: Sprawdzaj logi Agent Gateway w Azure Log Analytics dla lokalnego projektu agentgateway-aca-consumption-demo. Użyj, gdy trzeba ustalić cel logowania, dostępność i świeżość logów, błędy konsoli lub platformy ACA albo stan telemetrii Application Insights.
---

# Logi Agent Gateway w Azure

## Ustal, gdzie trafiają logi

Zacznij od `infra/iam-poc.foundation.bicepparam`, `infra/iam-poc.gateway.bicepparam` i sekcji „Gateway telemetry” w `infra/README.md`. Domyślne wdrożenie to subskrypcja `Test` (`7e7963c6-cddc-4d64-bcdd-1bfb727a05c2`), grupa `iam-poc`, aplikacja `agentgateway-iam-poc`, środowisko `acae-iam-poc` i oczekiwany workspace `log-iam-poc`. Jeśli użytkownik wskazał inne wdrożenie tego projektu, zweryfikuj jego zasoby osobno.

Użyj bieżącej sesji Azure CLI. Polecenia kieruj jawnie do właściwej subskrypcji. Jeśli sesja wygasła, zaloguj się do tenanta projektu za pomocą `az login --tenant 655ccf7b-6f5b-4110-86e3-45c4b8ffc39a --use-device-code` i podaj użytkownikowi URL oraz kod z CLI. Diagnostykę prowadź przez odczyt konfiguracji i zapytania. W wynikach pomijaj sekrety, tokeny i wrażliwą treść żądań.

```bash
agw_sub='7e7963c6-cddc-4d64-bcdd-1bfb727a05c2'
agw_rg='iam-poc'
agw_app='agentgateway-iam-poc'
agw_env='acae-iam-poc'

az containerapp show --subscription "$agw_sub" -g "$agw_rg" -n "$agw_app" \
  --query '{environmentId:properties.managedEnvironmentId || properties.environmentId,revision:properties.latestRevisionName,readyRevision:properties.latestReadyRevisionName,minReplicas:properties.template.scale.minReplicas}' -o json
az containerapp env show --subscription "$agw_sub" -g "$agw_rg" -n "$agw_env" \
  --query '{destination:properties.appLogsConfiguration.destination,customerId:properties.appLogsConfiguration.logAnalyticsConfiguration.customerId}' -o json
az monitor log-analytics workspace show --subscription "$agw_sub" -g "$agw_rg" -n log-iam-poc \
  --query '{id:id,customerId:customerId,retentionDays:retentionInDays}' -o json
```

Jeśli aplikacja wskazuje inne środowisko, sprawdź środowisko z jej identyfikatora. Przy `log-analytics` porównaj `customerId` środowiska i workspace’u. Przy `azure-monitor` sprawdź ustawienia diagnostyczne środowiska i aplikacji przez `az monitor diagnostic-settings list --resource <ARM-ID> --subscription "$agw_sub"`. Zweryfikuj włączone kategorie i miejsce docelowe. Zamień ARM ID docelowego workspace’u na jego `customerId`. Jeśli cel wskazuje inny workspace lub inne miejsce przechowywania, podaj ustalony cel i zakres faktycznego dostępu.

## Sprawdź dostępność i błędy

Wykonaj zapytania osobno dla konsoli i systemu. Domyślnie sprawdź ostatnie 24 godziny. Przy pustym wyniku sprawdź ostatnie 7 dni w granicach retencji, chyba że użytkownik podał własne okno. Dla `log-analytics` użyj tabel `ContainerAppConsoleLogs_CL` i `ContainerAppSystemLogs_CL`. Dla `azure-monitor` dopasuj zapytanie do rzeczywistych tabel i schematu, np. `ContainerAppConsoleLogs` oraz `ContainerAppSystemLogs` z kolumnami bez sufiksu `_s`. Jeśli ścieżka logowania się zmieniła, sprawdź obie serie dla okresu obejmującego zmianę.

```bash
# agw_workspace ustaw na zweryfikowany GUID customerId.
az monitor log-analytics query --subscription "$agw_sub" --workspace "$agw_workspace" \
  --timespan PT24H --analytics-query "
ContainerAppConsoleLogs_CL
| where ContainerAppName_s == '$agw_app'
| summarize Entries = count(), FirstSeen = min(TimeGenerated), LastSeen = max(TimeGenerated)
" -o table
```

Dla błędów zastąp `summarize` poniższym fragmentem. Powtórz oba zapytania dla tabeli systemowej. Jeśli sprawdzasz awarię całego środowiska, wykonaj dodatkowe zapytanie systemowe z filtrem `EnvironmentName_s` i pustą nazwą aplikacji.

```kusto
| extend Level = tostring(column_ifexists("LogLevel_s", "")),
         Reason = tostring(column_ifexists("Reason_s", ""))
| where Level in~ ("warn", "warning", "error", "fatal", "critical")
    or Reason in~ ("Unhealthy", "BackOff", "Failed")
    or Log_s has_any ("error", "failed", "panic", "exception", "unauthorized", "forbidden")
    or Log_s contains "timeout"
| summarize Entries = count(), LastSeen = max(TimeGenerated) by RevisionName_s, Level, Reason, Log_s
| top 25 by LastSeen desc
```

Sprawdź kontekst dopasowań. Rozróżnij problemy startu, obrazu i sond ACA od błędów konfiguracji gatewaya, Managed Identity i backendu. Oczekiwane odrzucenia przez politykę MCP oznacz osobno. Jeśli potrzebujesz kontekstu, pobierz krótką próbkę z tej samej rewizji i przedziału czasu.

Przy pustych wynikach sprawdź stan rewizji i możliwość zejścia do zera replik. W razie potrzeby podejrzyj ostatnie wpisy przez `az containerapp logs show --subscription "$agw_sub" -g "$agw_rg" -n "$agw_app" --type system --tail 30` lub `--type console`. Zaznacz, jeśli wpisy są widoczne tylko w strumieniu ACA. Brak uprawnień, brak tabeli, błąd zapytania i poprawne zapytanie z zerową liczbą wpisów to osobne wyniki.

## Telemetria i raport

Jeśli trzeba sprawdzić żądania lub zależności, zweryfikuj wdrożenie konfiguracji OpenTelemetry z `infra/foundation.bicep` i `infra/gateway.bicep`. Ustal workspace powiązany z Application Insights. Sprawdź `AppRequests` i `AppDependencies` w tym workspace, filtrując po potwierdzonej roli gatewaya i zasobie Application Insights. Koreluj po `OperationId`, jeśli jest dostępny. Dla błędów HTTP uwzględnij `Success` i `ResultCode`. W razie braku wyników uwzględnij brak ruchu, sampling i stan wdrożenia eksportera. Zapis śladu żądania i logi konsoli oraz systemu raportuj osobno.

Podaj potwierdzone miejsce docelowe, zakres UTC, dostępność każdej sprawdzonej tabeli, liczbę i czas ostatniego wpisu oraz najważniejsze błędy z rewizją i czasem. Podaj, czego nie udało się zweryfikować i dlaczego. Ogranicz wniosek o braku błędów do sprawdzonych danych i użytych filtrów.

Dokumentacja: [Monitorowanie logów ACA](https://learn.microsoft.com/azure/container-apps/log-monitoring), [Cele logowania ACA](https://learn.microsoft.com/azure/container-apps/log-options), [Zapytania Log Analytics w Azure CLI](https://learn.microsoft.com/cli/azure/monitor/log-analytics#az-monitor-log-analytics-query).
