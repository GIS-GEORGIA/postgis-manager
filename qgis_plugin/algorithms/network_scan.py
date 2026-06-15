"""Network Scan for PostGIS instances — Processing algorithm."""

from qgis.core import (
    QgsProcessingAlgorithm, QgsProcessingParameterString,
    QgsProcessingParameterNumber, QgsProcessingOutputString,
    QgsProcessingContext, QgsProcessingFeedback,
)
from qgis.PyQt.QtCore import QCoreApplication


class NetworkScanAlgorithm(QgsProcessingAlgorithm):
    CIDR    = "CIDR"
    PORTS   = "PORTS"
    TIMEOUT = "TIMEOUT"
    WORKERS = "WORKERS"
    OUTPUT  = "OUTPUT"

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterString(
            self.CIDR, self.tr("IP range / CIDR"), defaultValue="192.168.1.0/24"))
        self.addParameter(QgsProcessingParameterString(
            self.PORTS, self.tr("Ports"), defaultValue="5432,5433"))
        self.addParameter(QgsProcessingParameterNumber(
            self.TIMEOUT, self.tr("TCP timeout (s)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=0.5, minValue=0.1, maxValue=10.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.WORKERS, self.tr("Parallel threads"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=64, minValue=1, maxValue=256))
        self.addOutput(QgsProcessingOutputString(
            self.OUTPUT, self.tr("Found instances (JSON)")))

    def processAlgorithm(self, parameters, context, feedback):
        cidr    = self.parameterAsString(parameters, self.CIDR, context)
        ports   = self.parameterAsString(parameters, self.PORTS, context)
        timeout = self.parameterAsDouble(parameters, self.TIMEOUT, context)
        workers = self.parameterAsInt(parameters, self.WORKERS, context)

        extra_ports = []
        for p in ports.split(","):
            try:
                extra_ports.append(int(p.strip()))
            except ValueError:
                pass

        try:
            import sys, os, json
            plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if plugin_dir not in sys.path:
                sys.path.insert(0, plugin_dir)

            from postgis_manager.utils.network_scanner import scan_network

            found = []

            def on_found(result):
                found.append(result)
                feedback.pushInfo(
                    f"Found: {result.get('host')}:{result.get('port')} "
                    f"— {result.get('pg_version','?')}")

            def on_progress(done, total):
                feedback.setProgress(int(100 * done / max(total, 1)))

            stop_flag = {"stop": False}

            def check_cancel():
                if feedback.isCanceled():
                    stop_flag["stop"] = True

            scan_network(
                cidr,
                extra_ports=extra_ports,
                tcp_timeout=timeout,
                workers=workers,
                on_found=on_found,
                on_progress=on_progress,
                stop_flag=stop_flag,
            )

            output = json.dumps(found, indent=2)
            feedback.pushInfo(f"\nTotal found: {len(found)}")
            return {self.OUTPUT: output}
        except Exception as e:
            raise Exception(f"Scan failed: {e}") from e

    def name(self): return "networkscan"
    def displayName(self): return self.tr("Scan network for PostgreSQL instances")
    def group(self): return self.tr("Database")
    def groupId(self): return "database"
    def createInstance(self): return NetworkScanAlgorithm()
    def tr(self, s): return QCoreApplication.translate("NetworkScanAlgorithm", s)
