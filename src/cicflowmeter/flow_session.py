from scapy.packet import Packet
from scapy.sessions import DefaultSession

from cicflowmeter.writer import output_writer_factory

from .constants import EXPIRED_UPDATE, GARBAGE_COLLECT_PACKETS
from .features.context import PacketDirection, get_packet_flow_key
from .flow import Flow
from .utils import get_logger

#  Inherits from scapy DefaultSession - look into
class FlowSession(DefaultSession): 
    """Creates a list of network flows."""

    verbose = False
    fields = None
    output_mode = None
    output = None

    def __init__(self, *args, **kwargs):
        self.flows: dict[tuple, Flow] = {}
        self.logger = get_logger(self.verbose)
        self.packets_count = 0
        self.output_writer = output_writer_factory(self.output_mode, self.output)

        super(FlowSession, self).__init__(*args, **kwargs)
        print("creating flow session class")

    def toPacketList(self):
        # Sniffer finished all the packets it needed to sniff.
        # It is not a good place for this, we need to somehow define a finish signal for AsyncSniffer
        self.garbage_collect(None)
        del self.output_writer
        return super(FlowSession, self).toPacketList()

    def process(self, pkt: Packet):
        """
        Needed for use in scapy versions above 2.5 because of a breaking change in scapy. 
        Functionality is same as on_packet_received, but returnvalues are added. 
        """
        # print("Flow session class processing packets")
        self.logger.debug(f"Packet {self.packets_count}: {pkt}")
        count = 0
        direction = PacketDirection.FORWARD

        if "TCP" not in pkt and "UDP" not in pkt:
            return pkt  # Return the processed packet

        try:
            # Creates a key variable to check
            packet_flow_key = get_packet_flow_key(pkt, direction)
            flow = self.flows.get((packet_flow_key, count))
        except Exception:
            return pkt  # Return the processed packet 

        self.packets_count += 1
        

        # If there is no forward flow with a count of 0
        if flow is None:
            # There might be one of it in reverse
            direction = PacketDirection.REVERSE
            packet_flow_key = get_packet_flow_key(pkt, direction)
            flow = self.flows.get((packet_flow_key, count))

        if flow is None:
            # If no flow exists create a new flow
            direction = PacketDirection.FORWARD
            flow = Flow(pkt, direction)
            packet_flow_key = get_packet_flow_key(pkt, direction)
            self.flows[(packet_flow_key, count)] = flow

        elif (pkt.time - flow.latest_timestamp) > EXPIRED_UPDATE:
            # If the packet exists in the flow but the packet is sent
            # after too much of a delay than it is a part of a new flow.
            expired = EXPIRED_UPDATE
            while (pkt.time - flow.latest_timestamp) > expired:
                count += 1
                expired += EXPIRED_UPDATE
                flow = self.flows.get((packet_flow_key, count))

                if flow is None:
                    flow = Flow(pkt, direction)
                    self.flows[(packet_flow_key, count)] = flow
                    break
        elif "F" in pkt.flags:
            # If it has FIN flag then early collect flow and continue
            flow.add_packet(pkt, direction)
            self.garbage_collect(pkt.time)
            return None # Return None to indicate processing is incomplete

        # print(pkt)
        flow.add_packet(pkt, direction)

        # if self.packets_count % GARBAGE_COLLECT_PACKETS == 0 or flow.duration > 120:
        if self.packets_count % GARBAGE_COLLECT_PACKETS == 0 or flow.duration > 5:
            self.garbage_collect(pkt.time)

        
        return pkt  # Return the processed packet

    def get_flows(self):
        # flow session returning flows
        print("flow session getting flows")
        return self.flows.values()

    def garbage_collect(self, latest_time) -> None:
        # print("garbage collection")
        # TODO: Garbage Collection / Feature Extraction should have a separate thread
        # print(f"flow keys list: {list(self.flows.keys())}")
        for k in list(self.flows.keys()):
            flow = self.flows.get(k)

            if not flow or (
                latest_time is not None
                and latest_time - flow.latest_timestamp < EXPIRED_UPDATE
                and flow.duration < 90
            ):
                continue
            print("writing to writer")
            self.output_writer.write(flow.get_data(self.fields))
            del self.flows[k]
            self.logger.debug(f"Flow Collected! Remain Flows = {len(self.flows)}")
