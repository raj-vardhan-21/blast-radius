import resource
import subprocess
import sys

for m in sys.argv[1:]:
    b = resource.getrusage(resource.RUSAGE_CHILDREN)
    subprocess.run([sys.executable, "-c", m])
    a = resource.getrusage(resource.RUSAGE_CHILDREN)
    print(f"{a.ru_utime - b.ru_utime + a.ru_stime - b.ru_stime:.3f}s  {m}")
