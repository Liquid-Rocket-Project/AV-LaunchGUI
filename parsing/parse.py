

DATA_FILE = "sf11-19dump.txt"
# DATA_FILE = "test.txt"
OUT_FILE = "sf11-19parsed.csv"

if __name__ == "__main__":

    with open(DATA_FILE, "r") as f:
        lines = f.readlines()
        l = [line.split("->") for line in lines if "," in line]
    
    for item in l:
        item[0] = item[0].split("| ")[1].strip()
        item[1] = [x.strip() for x in item[1].split(",")]

    with open(OUT_FILE, "w+") as f:
        f.write("time,PT1: HP,PT2: FUEL,PT3: OX,PT4: ENG?, PT5,PT6,PT7,PT8\n")
        for item in l:
            f.write(f"{item[0]},{','.join(item[1])}\n")

    

