import inspect
import edge_tts.communicate as c

source = inspect.getsource(c.Communicate.__init__)
with open("tmp_explore_out.txt", "w") as f:
    f.write(source)
