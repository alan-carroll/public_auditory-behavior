import asyncio
from twisted.internet import asyncioreactor
asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # Some necessary Windows line
try:
    asyncioreactor.install(asyncio.get_event_loop())
except:
    pass
import GUI
from twisted.internet import reactor


if __name__ == "__main__":
    client_gui = GUI.client.create_gui()
    reactor.run()

