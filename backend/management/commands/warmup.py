from django.core.management.base import BaseCommand, CommandError
from django.db.models import TextChoices
from backend.models import Backend
import requests


class Command(BaseCommand):
    help = 'Usage: python manage.py warmup <backend name / slug / id> [target]'
    help += '\npossible targets are:'
    help += '\n  - clear_and_pin  (default)'
    help += '\n  - pin'
    help += '\n  - clear'
    help += '\n  - clear-unpinned'
    help += '\n  - show-all-ac-queries'

    def __init__(self, *args, **kwargs):
        self._logs = []
        super().__init__( *args, **kwargs)

    class Targets(TextChoices):
        CLEAR_AND_PIN = "clear_and_pin", "Clear and pin"
        PIN = "pin", "Pin"
        CLEAR = "clear", "Clear"
        CLEAR_UNPINNED = "clear_unpinned", "Clear unpinned"
        SHOW_ALL_AC_QUERIES = "show_all_ac_queries", "Show all autocompletion queries"
    
    PRINT_FORMATS = {
        "red": lambda text: f"\033[31m{text}\033[0m",
        "blue": lambda text: f"\033[34m{text}\033[0m",
        "bold": lambda text: f"{text}\033[0m",
        None: lambda text: text,
    }

    HTML_FORMATS = {
        "red": lambda text: f'<span style="color: red">{text}</span>',
        "blue": lambda text: f'<span style="color: blue">{text}</span>',
        "bold": lambda text: f'<strong>{text}</strong>',
        None: lambda text: text,
    }

    def add_arguments(self, parser):
        parser.add_argument('backend', nargs=1,
                            help='Id, Slug or Name of a Backend')
        parser.add_argument(
            'target', nargs='?', default="clear_and_pin", help='Id, Slug or Name of a Backend')
    

    
    def log(self, msg, format=None, *args):
        if args:
            msg += " " + " ".join(str(arg) for arg in args)
        
        
        printMsg = self.PRINT_FORMATS[format](msg)
        htmlMsg = self.HTML_FORMATS[format](msg)
        self._logs.append(htmlMsg)
        print(printMsg)

    def handle(self, *args, returnLog=False, **options):
        target = options["target"]
        backend = options["backend"][0]

        backends = Backend.objects.filter(
            name=backend) | Backend.objects.filter(slug=backend)

        try:
            backends = backends | Backend.objects.filter(id=backend)
        except (ValueError, TypeError):
            pass

        backend = backends.first()

        if not backend:
            raise CommandError(
                "Please specify a Backend by providing it's name, slug or ID"
            )

        self.backend = backend

        if target == self.Targets.CLEAR_AND_PIN:
            self.clear()
            self.pin()
            self.clear(onlyUnpinned=True)
        elif target == self.Targets.PIN:
            self.pin()
        elif target == self.Targets.CLEAR:
            self.clear()
        elif target == self.Targets.CLEAR_UNPINNED:
            self.clear(onlyUnpinned=True)
        elif target == self.Targets.SHOW_ALL_AC_QUERIES:
            self.showAutocompleteQueries()
        else:
            raise CommandError("Unknown target: " + target)

        if returnLog:
            return self._logs

    def clear(self, onlyUnpinned=False):
        if onlyUnpinned:
            msg = "Clear cache, but only the unpinned results"
            params = {"cmd": "clearcache"}
        else:
            msg = "Clear cache completely, including the pinned results"
            params = {"cmd": "clearcachecomplete"}
        self.log(msg, format="bold")
        response = requests.get(self.backend.baseUrl, params=params)
        response.raise_for_status()

    def pin(self):
        prefixString = self._getPrefixString()

        warmups = (
            (self.backend.warmupQuery1,
             "Entities names aliases score, ordered by score, full result for Subject AC query with empty prefix"),
            (self.backend.warmupQuery2,
             "Entities names aliases score, ordered by alias, part of Subject AC query with non-empty prefix"),
            (self.backend.warmupQuery3,
             "Entities names aliases score, ordered by entity, part of Object AC query"),
            (self.backend.warmupQuery4,
             "Predicates names aliases score, without prefix (only wdt: and schema:about)"),
            (self.backend.warmupQuery5,
             "Predicates names aliases score, with prefix (all predicates)"),
        )

        # pin warmup queries
        for warmup, headline in warmups:
            self.log(f"\nPin: {headline}", format="bold")
            warmupQuery = self._buildQuery(warmup)
            self.log(warmupQuery)
            self._pinQuery(f"{prefixString} {warmupQuery}")

        # pin frequent predicates
        for predicate in self.backend.frequentPredicates.split(" "):
            if not predicate:
                continue
            self.log(f"\nPin: {predicate} ordered by subject", format="bold")
            query = f"{prefixString}\nSELECT ?x ?y WHERE {{ ?x {predicate} ?y }} ORDER BY ?x"
            self.log(query)
            self._pinQuery(query)

            self.log(f"\nPin: {predicate} ordered by object", format="bold")
            query = f"{prefixString}\nSELECT ?x ?y WHERE {{ ?x {predicate} ?y }} ORDER BY ?y"
            self.log(query)
            self._pinQuery(query)

        # pin frequent patterns
        for pattern in self.backend.frequentPatternsWithoutOrder.split(" "):
            if not pattern:
                continue
            self.log(f"\nPin: {pattern} without ORDER BY", format="bold")
            query = f"{prefixString}\nSELECT ?x ?y WHERE {{ ?x {pattern} ?y }}"
            self.log(query)
            self._pinQuery(query)

    def showAutocompleteQueries(self):
        self.log("\nSubject AC query\n", format="bold")
        self.log(self._buildQuery(self.backend.suggestSubjects))
        self.log("\nPredicate AC query\n", format="bold")
        self.log(self._buildQuery(self.backend.suggestSubjects))
        self.log("\nObject AC query\n", format="bold")
        self.log(self._buildQuery(self.backend.suggestSubjects))

    def _buildQuery(self, completionQuery):
        substitutionFinished = True
        for placeholder, replacement in self.backend.getWarmupAndAcPlaceholders().items():
            newQuery = completionQuery.replace(f"%{placeholder}%", replacement)
            if (newQuery != completionQuery):
                substitutionFinished = False
                completionQuery = newQuery

        if substitutionFinished:
            # replace prefixes
            if "%PREFIXES%" in completionQuery:
                prefixString = self._getPrefixString() + "\n%PREFIXES%"
                completionQuery = completionQuery.replace(
                    "%PREFIXES%", prefixString)
            return completionQuery
        else:
            return self._buildQuery(completionQuery)

    def _getPrefixString(self):
        prefixString = "\n".join(
            [f"PREFIX {prefixName}: <{prefix}>" for prefixName, prefix in self.backend.availablePrefixes.items()])
        return prefixString

    def _pinQuery(self, query):
        params = {"pinresult": "true", "send": "10", "query": query}
        response = requests.get(self.backend.baseUrl, params=params)
        response.raise_for_status()
        jsonData = response.json()
        if "exception" in jsonData:
            self.log("ERROR:", jsonData["exception"], format="red")
        else:
            self.log(f"Result size: {jsonData['resultsize']:,}", format="blue")
