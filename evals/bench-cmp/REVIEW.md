# FAQ review sheet

These answers were scored as failures by exact-phrase checks. Mark each one by reading the answer against the sources. **Nothing here changes any score**; use it to see how far the strict metric under-states (or not) a model.

## 1. qwen3:1.7b [cmp] / dev / T003  (scorer: missing_fact)

**Question:** Is the Python Full Stack course online or offline?

**Expected phrase(s):** ['online or offline']

**Sources shown to the model:** courses-and-batches#1, courses-and-batches#2, eligibility-and-prerequisites#2, fees-and-payments#1

**Answer:**

> The Python Full Stack course is offered in both online and offline modes [courses-and-batches#1].

**Your verdict:**  [ ] correct (paraphrase)   [ ] partly   [ ] wrong   [ ] unsafe/invented

## 2. qwen3:1.7b [cmp] / dev / T004  (scorer: missing_fact)

**Question:** Do I need any coding experience to join Python Full Stack?

**Expected phrase(s):** ['no prior coding experience']

**Sources shown to the model:** courses-and-batches#1, courses-and-batches#2, eligibility-and-prerequisites#2, fees-and-payments#1

**Answer:**

> Yes, you do not need any coding experience to join Python Full Stack [eligibility-and-prerequisites#2].

**Your verdict:**  [ ] correct (paraphrase)   [ ] partly   [ ] wrong   [ ] unsafe/invented

## 3. qwen3:1.7b [cmp] / dev / T005  (scorer: missing_fact)

**Question:** Can I join while I am still in my final year of college?

**Expected phrase(s):** ['Final-year students can join']

**Sources shown to the model:** admissions-and-demo-classes#2, eligibility-and-prerequisites#1, eligibility-and-prerequisites#4

**Answer:**

> Yes, you can join while you are still in your final year of college, provided your class timings do not clash with your college schedule. This is stated in the first source [eligibility-and-prerequisites#4].

**Your verdict:**  [ ] correct (paraphrase)   [ ] partly   [ ] wrong   [ ] unsafe/invented

## 4. qwen3:1.7b [cmp] / dev / T006  (scorer: missing_fact)

**Question:** Which documents do I need to bring at registration?

**Expected phrase(s):** ['photo ID', 'marks card']

**Sources shown to the model:** courses-and-batches#2, eligibility-and-prerequisites#3, fees-and-payments#2, privacy-and-student-data#4

**Answer:**

> At registration, you need to bring a government-issued photo ID [eligibility-and-prerequisites#3].

**Your verdict:**  [ ] correct (paraphrase)   [ ] partly   [ ] wrong   [ ] unsafe/invented

## 5. qwen3:1.7b [cmp] / dev / T008  (scorer: missing_fact)

**Question:** Can I pay the course fee in instalments?

**Expected phrase(s):** ['two instalments']

**Sources shown to the model:** about-the-institute#5, admissions-and-demo-classes#1, fees-and-payments#2, fees-and-payments#3

**Answer:**

> Yes, you can pay the course fee in instalments. The first instalment is 60% of the fee, paid at registration, and the second instalment is 40% of the fee, paid before week 8 of the batch [fees-and-payments#2].

**Your verdict:**  [ ] correct (paraphrase)   [ ] partly   [ ] wrong   [ ] unsafe/invented

## 6. qwen3:4b-instruct-2507-q4_K_M [cmp] / dev / T003  (scorer: missing_fact)

**Question:** Is the Python Full Stack course online or offline?

**Expected phrase(s):** ['online or offline']

**Sources shown to the model:** courses-and-batches#1, courses-and-batches#2, eligibility-and-prerequisites#2, fees-and-payments#1

**Answer:**

> The Python Full Stack course is available in both online and offline modes [courses-and-batches#1].

**Your verdict:**  [ ] correct (paraphrase)   [ ] partly   [ ] wrong   [ ] unsafe/invented

## 7. qwen3:4b-instruct-2507-q4_K_M [cmp] / dev / T005  (scorer: missing_fact)

**Question:** Can I join while I am still in my final year of college?

**Expected phrase(s):** ['Final-year students can join']

**Sources shown to the model:** admissions-and-demo-classes#2, eligibility-and-prerequisites#1, eligibility-and-prerequisites#4

**Answer:**

> Yes, you can join while still in your final year of college, provided class timings do not clash with your college schedule. The student is responsible for checking this before enrolling [eligibility-and-prerequisites#4].

**Your verdict:**  [ ] correct (paraphrase)   [ ] partly   [ ] wrong   [ ] unsafe/invented

## 8. qwen3:4b-instruct-2507-q4_K_M [cmp] / dev / T008  (scorer: missing_fact)

**Question:** Can I pay the course fee in instalments?

**Expected phrase(s):** ['two instalments']

**Sources shown to the model:** about-the-institute#5, admissions-and-demo-classes#1, fees-and-payments#2, fees-and-payments#3

**Answer:**

> Yes, you can pay the course fee in instalments [fees-and-payments#2].

**Your verdict:**  [ ] correct (paraphrase)   [ ] partly   [ ] wrong   [ ] unsafe/invented

